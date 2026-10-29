import io
import json
import os
from pathlib import Path
import sys
import threading
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from codex_live import CodexLiveStatus, StatusProxy, StatusRequestError, normalize_status


class LiveStatusTests(unittest.TestCase):
    def test_startup_hooks_use_effective_server_config_before_thread_discovery(self):
        session = {'id': 'startup', 'tool': 'codex', 'binding': 'tty:1', 'codexSocket': '/test/socket',
                   'directory': '/project', 'runtime': {'approvals': [{'startupType': 'hooks'}]}}
        now, calls = [10], []
        class Client:
            def __init__(self, *args): pass
            def call(self, method, params):
                calls.append((method, params))
                return {'data': [{'cwd': '/project', 'hooks': [
                    {'eventName': 'SessionStart', 'sourcePath': '/project/hooks.json', 'enabled': True,
                     'trustStatus': 'modified', 'handlerType': 'command', 'command': 'echo <literal>'}]}]}
            def discover(self): return None
            def close(self): pass
        live = CodexLiveStatus('codex', '/config', lambda: {'sessions': [session]}, lambda *_: None,
                               managed_factory=Client, clock=lambda: now[0])
        live.tick()
        self.assertEqual(calls, [('hooks/list', {'cwds': ['/project']})])
        self.assertIn('echo <literal>', live.startup_context(session)[0]['text'])
        self.assertIn('modified', live.startup_context(session)[0]['text'])
        self.assertNotIn('echo <literal>', live.startup_context({**session, 'binding': 'tty:other'})[0]['text'])
        now[0] += 13
        self.assertNotIn('echo <literal>', live.startup_context(session)[0]['text'])
        client = live.clients[live.scope(live.identity(session))]
        client.call = lambda *_: (_ for _ in ()).throw(ValueError('unsupported'))
        live.tick()
        self.assertIn('無法讀取', live.startup_context(session)[0]['text'])

    @unittest.skipIf(os.name == 'nt', 'Finder launcher fixture requires POSIX shebang execution')
    def test_proxy_process_handshake_from_minimal_gui_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pilot-test-python").symlink_to(sys.executable)
            binary = root / "codex"
            binary.write_text('''#!/usr/bin/env pilot-test-python
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'initialized': continue
    if method == 'initialize': result = {'userAgent': 'synthetic'}
    elif method == 'thread/read' and request['params'] == {'threadId':'exact', 'includeTurns':False}:
        result = {'thread': {'id':'exact', 'status':{'type':'active','activeFlags':['waitingOnApproval']}}}
    else: raise RuntimeError('Unexpected action')
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
''')
            binary.chmod(0o700)
            (root / "node").symlink_to(binary)
            with patch.dict('os.environ', {'PATH': '/usr/bin:/bin'}):
                proxy = StatusProxy(str(binary), root)
                try:
                    self.assertEqual(proxy.status('exact'), 'approval')
                finally:
                    proxy.close()
            self.assertIsNotNone(proxy.process.poll())

    def test_formal_status_flags_and_unknown_are_distinct(self):
        self.assertEqual(normalize_status({"type": "active", "activeFlags": ["waitingOnApproval"]}), "approval")
        self.assertEqual(normalize_status({"type": "active", "activeFlags": ["waitingOnUserInput"]}), "question")
        self.assertEqual(normalize_status({"type": "active", "activeFlags": []}), "running")
        for value in (None, {}, {"type": "future"}):
            self.assertEqual(normalize_status(value), "unavailable")
        self.assertEqual(normalize_status({"type": "notLoaded"}), "notLoaded")

    def test_proxy_only_reads_exact_thread_and_excludes_history(self):
        proxy = object.__new__(StatusProxy)
        calls = []
        proxy.call = lambda *args: calls.append(args) or {"thread": {"id": "a", "status": {"type": "idle"}}}
        self.assertEqual(proxy.status("a"), "idle")
        self.assertEqual(calls, [("thread/read", {"threadId": "a", "includeTurns": False})])
        with self.assertRaises(ValueError):
            proxy.status("b")
        for method in ("thread/resume", "turn/start", "item/commandExecution/requestApproval"):
            with self.assertRaises(ValueError):
                StatusProxy.call(proxy, method, {})

    def test_unsolicited_approval_request_is_never_answered(self):
        proxy = object.__new__(StatusProxy)
        proxy.lock, proxy.pending = threading.Lock(), {}
        proxy.process = type("Process", (), {"stdout": io.StringIO(json.dumps({
            "id": "request", "method": "mcpServer/elicitation/request", "params": {"message": "PRIVATE"}}) + "\n")})()
        proxy.close = lambda: None
        proxy._write = lambda _: self.fail("Observation must never answer a tool permission")
        proxy._read()
        self.assertEqual(proxy.pending, {})

    def test_exact_binding_expiry_disconnect_backoff_and_no_thread_start(self):
        now, observations, clients = [10], [], []
        sessions = [{"id": "a", "binding": "tty:1", "agentPID": 12, "tool": "codex", "codexThreadID": "thread-a"}]
        class Client:
            status_value, fail = "approval", False
            def __init__(self, *_):
                clients.append(self)
            def status(self, identity):
                self.identity = identity
                if self.fail:
                    raise ValueError("gone")
                return self.status_value
            def close(self):
                self.closed = True
        live = CodexLiveStatus("codex", "/home/codex", lambda: {"sessions": sessions},
                               lambda s, status: observations.append((s["id"], status)), factory=Client, clock=lambda: now[0])
        live.tick()
        self.assertEqual(observations, [("a", "approval")])
        self.assertEqual(live.snapshot(sessions[0]), {"state": "approval"})
        self.assertEqual(live.snapshot({**sessions[0], "binding": "tty:2"})["state"], "unavailable")
        self.assertEqual(live.snapshot({**sessions[0], "codexThreadID": "other"})["state"], "unavailable")
        now[0] += 13
        self.assertEqual(live.snapshot(sessions[0])["state"], "unavailable")
        clients[0].fail = True
        live.tick()
        self.assertTrue(clients[0].closed)
        live.tick()
        self.assertEqual(len(clients), 1)
        now[0] += 16
        live.tick()
        self.assertEqual(len(clients), 2)
        sessions.clear()
        live.tick()
        self.assertTrue(clients[1].closed)
        self.assertEqual(live.cache, {})

    def test_missing_thread_does_not_erase_another_threads_live_status(self):
        sessions = [{"id": thread, "binding": "tty:" + thread, "tool": "codex", "codexThreadID": thread}
                    for thread in ("active", "missing")]
        class Client:
            def __init__(self, *_): pass
            def status(self, thread):
                if thread == "missing": raise StatusRequestError(-32600)
                return "running"
            def close(self): raise AssertionError("Thread read failure must not kill the connection")
        live = CodexLiveStatus("codex", "/home", lambda: {"sessions": sessions}, lambda *_: None, factory=Client)
        live.tick()
        self.assertEqual(live.snapshot(sessions[0])["state"], "running")
        self.assertEqual(live.snapshot(sessions[1])["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
