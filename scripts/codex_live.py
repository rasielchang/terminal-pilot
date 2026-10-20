"""Exact-thread runtime monitoring through the local Codex app-server.

Legacy connections are read-only. Managed connections use ApprovalProxy to join
the already-loaded TUI thread and forward explicit user decisions. No prompts,
credentials or approval payloads are persisted by this monitor.
"""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
import json
import os
from pathlib import Path
import subprocess
import shutil
import threading
import time
import uuid
from runtime_paths import find_tool


class StatusRequestError(ValueError):
    def __init__(self, code, detail=None):
        self.code = code if isinstance(code, int) else None
        self.detail = detail  # In-memory diagnostic only; never sent to the browser or logged.
        super().__init__("Codex status request failed")


class StatusProxy:
    methods = {"initialize", "thread/read"}

    def __init__(self, binary, home, *, socket=None, capabilities=None):
        self.lock = threading.RLock()
        self.pending = {}
        self.closed = False
        environment = {**os.environ, "CODEX_HOME": str(home)}
        # Finder-launched apps do not inherit the interactive shell's Node PATH.
        environment["PATH"] = str(Path(binary).parent) + os.pathsep + environment.get("PATH", "")
        bundled = Path(__file__).resolve().parent / 'runtime/node/bin/node'
        node = str(bundled) if bundled.is_file() else shutil.which('node', path=environment['PATH']) or find_tool('node')
        if not node: raise ValueError('Node runtime unavailable')
        self.process = subprocess.Popen(
            [node, str(Path(__file__).with_name('codex_socket.js')),
             str(socket or Path(home) / "app-server-control/app-server-control.sock")],
            env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {"name": "terminal_pilot", "version": "0.9.0"},
                                     **({"capabilities": capabilities} if capabilities else {})})
            self._write({"method": "initialized", "params": {}})
        except (OSError, ValueError):
            self.close()
            raise

    def _write(self, value):
        with self.lock:
            if self.closed:
                raise ValueError("Codex status disconnected")
            self.process.stdin.write(json.dumps(value) + "\n")
            self.process.stdin.flush()

    def _read(self):
        try:
            # Runtime reads exclude turns; reject unexpectedly large frames.
            while line := self.process.stdout.readline(1_000_001):
                if len(line) > 1_000_000:
                    break
                value = json.loads(line)
                if not isinstance(value, dict):
                    continue
                if "method" in value:
                    self.event(value)
                    continue
                with self.lock:
                    future = self.pending.pop(value.get("id"), None)
                if future:
                    if "error" in value:
                        future.set_exception(StatusRequestError(value["error"].get("code"), value["error"].get("message")))
                    else:
                        future.set_result(value.get("result", {}))
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def event(self, value):
        pass  # Read-only legacy connection. ApprovalProxy handles explicit user decisions.

    def call(self, method, params):
        if method not in self.methods:
            raise ValueError("Status proxy is read-only")
        identity, future = str(uuid.uuid4()), Future()
        with self.lock:
            if self.closed:
                raise ValueError("Codex status disconnected")
            self.pending[identity] = future
        try:
            self._write({"id": identity, "method": method, "params": params})
            return future.result(timeout=4)
        except TimeoutError:
            self.close()
            raise ValueError("Codex status timed out") from None
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def status(self, thread_id):
        result = self.call("thread/read", {"threadId": thread_id, "includeTurns": False})
        thread = result.get("thread", {})
        if thread.get("id") != thread_id:
            raise ValueError("Codex thread mismatch")
        return normalize_status(thread.get("status"))

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            pending, self.pending = self.pending, {}
        for future in pending.values():
            future.set_exception(ValueError("Codex status disconnected"))
        # This is our proxy subprocess only, never the Codex daemon or TUI.
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def normalize_status(status):
    if not isinstance(status, dict):
        return "unavailable"
    kind = status.get("type")
    if kind == "active":
        flags = status.get("activeFlags", [])
        if "waitingOnApproval" in flags:
            return "approval"
        if "waitingOnUserInput" in flags:
            return "question"
        return "running"
    return {"idle": "idle", "systemError": "error", "notLoaded": "notLoaded"}.get(kind, "unavailable")


class CodexLiveStatus:
    def __init__(self, binary, default_home, state, observe, *, factory=StatusProxy, clock=time.monotonic,
                 bind=None, lifecycle=None, managed_factory=None, diagnostic=None):
        self.binary, self.default_home, self.state, self.observe = binary, default_home, state, observe
        self.factory, self.clock = factory, clock
        self.bind, self.lifecycle, self.managed_factory = bind, lifecycle, managed_factory
        self.diagnostic = diagnostic
        self.lock = threading.RLock()
        self.cache, self.clients, self.retry, self.errors = {}, {}, {}, {}
        self.stop_event = threading.Event()

    def identity(self, session):
        return (session.get("id"), session.get("binding"), session.get("codexThreadID"),
                session.get("agentPID"), session.get("codexSocket") or "",
                str(Path(session.get("codexHome") or self.default_home).resolve()))

    def scope(self, key):
        return (key[-1], key[-2]) if key[-2] else key[-1]

    def snapshot(self, session):
        key = self.identity(session)
        with self.lock:
            entry = self.cache.get(key)
            client = self.clients.get(self.scope(key))
            if not entry or self.clock() - entry[0] > 12:
                return {"state": "unavailable", "reason": self.errors.get(self.scope(key), "notConnected")}
        result = {"state": entry[1]}
        if key[-2] and (not client or client.closed):
            return {'state':'unavailable', 'reason':'connectionFailed'}
        if key[-2] and client and not client.closed:
            result.update(canApprove=session.get('codexThreadID') in client.subscribed,
                          approvals=client.approvals(session.get('codexThreadID')))
        return result

    def decide(self, session, data):
        key = self.identity(session)
        with self.lock:
            entry = self.cache.get(key)
            client = self.clients.get(self.scope(key))
        if (not key[-2] or not client or not entry or self.clock() - entry[0] > 12
                or not session.get('binding') or data.get('binding') != session['binding']):
            raise ValueError('核准連線已失效，請重新整理這個工作。')
        return client.decide(session['codexThreadID'], data.get('approval'), data.get('requestID'),
                             data.get('action'), data.get('content'))

    def brain_call(self, session, method, params):
        """Internal, exact-thread RPC. No HTTP route accepts a method or params."""
        if method not in {'turn/start', 'thread/turns/list', 'thread/items/list'}:
            raise ValueError('不支援此 Brain 操作。')
        key = self.identity(session)
        with self.lock:
            entry = self.cache.get(key)
            client = self.clients.get(self.scope(key))
        if (not key[-2] or not key[1] or not key[2] or params.get('threadId') != key[2]
                or not client or client.closed or not entry or self.clock() - entry[0] > 12
                or key[2] not in client.subscribed):
            raise ValueError('Brain Codex 連線尚未就緒。')
        if method == 'turn/start' and client.status(key[2]) != 'idle':
            raise ValueError('Brain Codex 仍在執行或等待確認。')
        return client.call(method, params)

    def tick(self):
        sessions = self.state().get("sessions", [])
        active = set()
        for session in sessions:
            if (session.get("tool") != "codex" or not session.get("binding") or session.get('phase') == 'ended'
                    or not (session.get("codexThreadID") or session.get('codexSocket'))):
                continue
            key = self.identity(session)
            active.add(key)
            home, socket, scope = key[-1], key[-2], self.scope(key)
            if self.retry.get(scope, 0) > self.clock() or not self.binary:
                continue
            stage = 'connect'
            try:
                with self.lock: client = self.clients.get(scope)
                if client is None:
                    if socket:
                        from codex_managed import valid_socket
                        from codex_approvals import ApprovalProxy
                        if not self.managed_factory and not valid_socket(socket):
                            stage = 'validateSocket'
                            raise ValueError('Invalid managed socket')
                        client = (self.managed_factory or ApprovalProxy)(self.binary, home, socket)
                    else:
                        client = self.factory(self.binary, home)
                    with self.lock: self.clients[scope] = client
                thread = session.get('codexThreadID')
                stage = 'discover'
                if not thread and socket:
                    thread = client.discover()
                    if thread and self.bind: self.bind(session, thread)
                    continue  # Bind through the store; next native snapshot validates it.
                stage = 'readStatus'
                status = client.status(thread)
                if socket:
                    approvals = client.approvals(thread)
                    if approvals:
                        status = 'question' if all(a['kind'] == 'question' for a in approvals) else 'approval'
                    session = {**session, 'pendingApprovalKeys':client.waiting_keys(thread)}
                with self.lock:
                    self.cache[key] = (self.clock(), status)
                    self.errors.pop(scope, None)
                if socket and self.lifecycle:
                    for event in client.drain_lifecycle(thread): self.lifecycle(session, event)
                self.observe(session, status)
                self.report(session, 'ready', status)
            except StatusRequestError as error:
                self.report(session, stage, 'requestRejected', error.code)
                with self.lock:
                    self.cache[key] = (self.clock(), "unavailable")
                    self.errors[scope] = "requestRejected"
                if scope not in self.clients: self.retry[scope] = self.clock() + 15
            except (OSError, ValueError, TypeError) as error:
                self.report(session, stage, type(error).__name__)
                with self.lock: client = self.clients.pop(scope, None)
                if client: client.close()
                self.retry[scope] = self.clock() + 5
                with self.lock:
                    self.cache = {k: v for k, v in self.cache.items() if self.scope(k) != scope}
                    self.errors[scope] = "connectionFailed"
        with self.lock:
            self.cache = {k: v for k, v in self.cache.items() if k in active}
            expired = [self.clients.pop(scope) for scope in set(self.clients) - {self.scope(key) for key in active}]
        for client in expired: client.close()

    def report(self, session, stage, outcome, code=None):
        if self.diagnostic:
            self.diagnostic({'session':session.get('id'), 'stage':stage, 'outcome':outcome,
                             'code':code, 'at':time.time()})

    def run(self):
        try:
            while not self.stop_event.is_set():
                try: self.tick()
                except (OSError, ValueError, TypeError): pass
                self.stop_event.wait(2)
        finally:
            for client in list(self.clients.values()): client.close()
