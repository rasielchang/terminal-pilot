#!/usr/bin/env python3
"""Private web UI. Terminal access stays in the signed native parent via JSON pipes."""
from __future__ import annotations

import argparse
from collections import OrderedDict, deque
from concurrent.futures import Future, TimeoutError
from contextlib import closing
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from web_push import PushService, endpoint_id, private_write
from voice_service import VoiceService, MAX_VOICE_BODY
from codex_live import CodexLiveStatus
from brain_service import BrainService
from brain_codex import CodexBrain
from bridge import observe_codex_runtime, bind_codex_runtime, codex_runtime_event

MAX_BODY = 40_000
MAX_TRANSCRIPT = 8 * 1024 * 1024


class NativeBridge:
    def __init__(self):
        self.pending = {}
        self.lock = threading.Lock()
        self.output_lock = threading.Lock()

    def emit(self, value):
        with self.output_lock:
            sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def read(self, on_close, on_command=None):
        try:
            for line in sys.stdin:
                value = json.loads(line)
                if value.get("command") == "refresh-pairing" and on_command:
                    on_command()
                    continue
                with self.lock:
                    future = self.pending.pop(value.get("id"), None)
                if future:
                    if "error" in value:
                        future.set_exception(ValueError(value["error"]))
                    else:
                        future.set_result(value.get("result", {}))
        finally:
            with self.lock:
                pending, self.pending = self.pending, {}
            for future in pending.values():
                future.set_exception(ValueError("Terminal Pilot 已離線。"))
            on_close()

    def call(self, method, params=None):
        request_id = str(uuid.uuid4())
        future = Future()
        with self.lock:
            self.pending[request_id] = future
        self.emit({"id": request_id, "method": method, "params": params or {}, "deadline": time.time() + 12})
        try:
            return future.result(timeout=15)
        except TimeoutError:
            raise ValueError("Terminal Pilot 回應逾時；若剛送出訊息，請先查看工作是否已收到，勿重複送出。") from None
        finally:
            with self.lock:
                self.pending.pop(request_id, None)


def read_records(path):
    """Read a bounded tail; ignore an incomplete write at the end of a live rollout."""
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - MAX_TRANSCRIPT))
        if size > MAX_TRANSCRIPT:
            stream.readline()
        data = stream.read(MAX_TRANSCRIPT)
    records = []
    for line in data.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except (ValueError, UnicodeError):
            pass
    return records


def text_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(part.get("text", "") for part in content
                     if isinstance(part, dict) and part.get("type", "").lower() in {"text", "input_text", "output_text"}
                     and isinstance(part.get("text"), str))


def messages_from_records(records):
    # Prefer UI items: response_item also includes injected instructions and hidden context.
    messages, seen = [], set()
    for record in records:
        payload = record.get("payload", {})
        if record.get("type") != "event_msg" or not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind == "item_completed":
            item = payload.get("item", {})
            role = {"UserMessage": "user", "AgentMessage": "assistant"}.get(item.get("type"))
            text = text_content(item.get("content"))
            identity = item.get("id")
            phase = item.get("phase")
        elif kind in {"user_message", "agent_message"}:
            role = "user" if kind == "user_message" else "assistant"
            text = payload.get("message", "")
            identity = payload.get("id")
            phase = payload.get("phase")
        else:
            continue
        if not role or not isinstance(text, str) or not text.strip():
            continue
        identity = identity or hashlib.sha256((str(record.get("timestamp")) + role + text).encode()).hexdigest()[:24]
        if identity in seen:
            continue
        seen.add(identity)
        message = {"id": identity, "role": role, "text": text[-60_000:], "time": record.get("timestamp", "")}
        if role == "assistant" and phase in {"commentary", "final_answer"}:
            message["phase"] = phase
        messages.append(message)
    return messages[-150:]


def progress_from_records(records, previous=None):
    # Lifecycle metadata only. Steering messages do not restart the turn clock.
    progress = dict(previous or {})
    for record in records:
        payload = record.get('payload')
        if record.get('type') != 'event_msg' or not isinstance(payload, dict): continue
        kind, turn = payload.get('type'), payload.get('turn_id')
        if kind == 'task_started' and isinstance(turn, str):
            try:
                from datetime import datetime
                started = datetime.fromisoformat(record.get('timestamp', '').replace('Z', '+00:00')).timestamp()
            except (ValueError, TypeError, AttributeError): continue
            progress = {'state':'active', 'turnID':turn, 'startedAt':started}
        elif kind in {'task_complete', 'turn_aborted'} and progress.get('turnID') == turn:
            progress['state'] = 'complete' if kind == 'task_complete' else 'interrupted'
    return progress


def read_progress(path, previous=None):
    """Incremental lifecycle-only scan, independent of the bounded chat tail.

    Long turns can push task_started out of that tail. Retain only timestamps,
    turn IDs and the last complete byte offset, never tool or message payloads.
    """
    with path.open('rb') as stream:
        stat = os.fstat(stream.fileno())
        identity = (stat.st_dev, stat.st_ino)
        valid = previous and previous['identity'] == identity and previous['offset'] <= stat.st_size
        offset = previous['offset'] if valid else 0
        progress = dict(previous['progress']) if valid else {}
        stream.seek(offset)
        while True:
            line = stream.readline(256_000)
            if not line: break
            if not line.endswith(b'\n'):
                # Skip oversized payloads without retaining them. If a writer is
                # still appending this line, reread it next time after completion.
                while line and not line.endswith(b'\n'):
                    line = stream.readline(256_000)
                if not line: break
                offset = stream.tell()
                continue
            offset = stream.tell()
            if not any(kind in line for kind in (b'"task_started"', b'"task_complete"', b'"turn_aborted"')):
                continue
            try:
                record = json.loads(line)
                if isinstance(record, dict): progress = progress_from_records([record], progress)
            except (ValueError, UnicodeError): pass
        return {'identity': identity, 'offset': offset, 'progress': progress}


class CodexHistory:
    """Read only the exact conversation identified by a Pilot hook/turn. Never guess by cwd."""
    def __init__(self, home):
        self.home = Path(home).resolve()
        self.resolved = {}
        self.activities = {}
        self.lifecycle = OrderedDict()
        self.missing = {}
        self.lock = threading.Lock()

    def allowed(self, path):
        path = Path(path).resolve()
        return path.is_relative_to(self.home / "sessions") and path.suffix == ".jsonl" and path.is_file()

    def candidates(self, session):
        thread = session.get("codexThreadID")
        transcript = session.get("codexTranscript")
        if transcript and self.allowed(transcript):
            return [Path(transcript)]
        for database in sorted(self.home.glob("state_*.sqlite"), reverse=True):
            try:
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
                    if thread:
                        rows = db.execute("SELECT rollout_path FROM threads WHERE id = ?", (thread,)).fetchall()
                    else:
                        rows = db.execute("SELECT rollout_path FROM threads WHERE cwd = ? ORDER BY updated_at DESC LIMIT 64",
                                          (session.get("directory", ""),)).fetchall()
                candidates = [Path(row[0]) for row in rows if self.allowed(row[0])]
                if candidates:
                    return candidates
            except (sqlite3.Error, OSError, ValueError):
                continue
        # Compatibility with installations without the state database.
        if thread:
            return [p for p in self.home.glob(f"sessions/*/*/*/*{thread}.jsonl") if self.allowed(p)]
        return []

    def matches(self, path, session, records):
        try:
            with path.open("rb") as stream:
                meta = json.loads(stream.readline(256_000)).get("payload", {})
        except (OSError, ValueError):
            return False
        if meta.get("parent_thread_id") or isinstance(meta.get("source"), dict):
            return False
        thread = session.get("codexThreadID")
        if thread:
            return meta.get("id", meta.get("session_id")) == thread
        turns = {session.get("activeTurn"), session.get("lastTurn")} - {None, ""}
        return bool(turns) and any(isinstance(r.get("payload"), dict) and r["payload"].get("turn_id") in turns
                                   for r in records)

    def conversation(self, session):
        with self.lock:
            key = (session["id"], session.get("codexThreadID"), session.get("activeTurn"), session.get("lastTurn"))
            self.activities.pop(key, None)
            cached = self.resolved.get(key)
            if self.missing.get(key, 0) > time.monotonic():
                return []
            if cached and self.allowed(cached):
                candidates = [cached]
            else:
                candidates = self.candidates(session)
            for path in candidates:
                try:
                    records = read_records(path)
                    if self.matches(path, session, records):
                        self.resolved[key] = path
                        cursor = read_progress(path, self.lifecycle.get(path))
                        self.lifecycle[path] = cursor
                        self.lifecycle.move_to_end(path)
                        if len(self.lifecycle) > 128: self.lifecycle.popitem(last=False)
                        self.activities[key] = cursor['progress'].copy()
                        if len(self.resolved) > 128:
                            old = next(iter(self.resolved))
                            self.resolved.pop(old)
                            self.activities.pop(old, None)
                        return messages_from_records(records)
                except (OSError, ValueError):
                    continue
            self.missing[key] = time.monotonic() + 10
            if len(self.missing) > 128:
                self.missing.pop(next(iter(self.missing)))
        return []


def claude_messages(records, session_id):
    """Only visible human/assistant text from the exact main Claude conversation."""
    messages = OrderedDict()
    for record in records:
        role = record.get("type")
        if (role not in {"user", "assistant"} or record.get("sessionId") != session_id
                or record.get("isSidechain") or record.get("isMeta") or record.get("isCompactSummary")):
            continue
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != role:
            continue
        text = text_content(message.get("content"))
        if not text.strip() or (role == "user" and text.lstrip().startswith((
                "<local-command-caveat>", "<command-name>", "<local-command-stdout>"))):
            continue
        identity = record.get("uuid")
        if not isinstance(identity, str) or not identity:
            continue
        messages[identity] = {"id": identity, "role": role, "text": text[-60_000:],
                              "time": record.get("timestamp", "")}
    return list(messages.values())[-150:]


class ClaudeHistory:
    def __init__(self, home):
        self.home = Path(home).expanduser().resolve()

    def allowed(self, path, session_id):
        path = Path(path).resolve()
        # Main transcripts only: projects/<project>/<session UUID>.jsonl.
        return (path.parent.parent == self.home / "projects"
                and path.name == session_id + ".jsonl" and path.is_file())

    def conversation(self, session):
        identity = session.get("claudeSessionID")
        if not identity:
            # Older Pilot hooks did not retain Claude's session ID. New Claude
            # installs register the main CLI PID; require cwd + launch time too
            # so PID reuse cannot attach an unrelated conversation.
            try:
                pid = int(session["agentPID"])
                entry = json.loads((self.home / "sessions" / f"{pid}.json").read_text())
                if (pid > 0 and entry.get("pid") == pid and entry.get("cwd") == session.get("directory")
                        and entry.get("kind") == "interactive" and entry.get("entrypoint") == "cli"
                        and abs(float(entry["startedAt"]) / 1000 - float(session["createdAt"])) < 30):
                    identity = entry.get("sessionId")
            except (OSError, ValueError, TypeError, KeyError):
                pass
        try:
            session_id = str(uuid.UUID(identity))
        except (ValueError, TypeError, AttributeError):
            return []
        transcript = session.get("claudeTranscript")
        candidates = [Path(transcript)] if transcript else self.home.glob(f"projects/*/{session_id}.jsonl")
        for path in candidates:
            try:
                if self.allowed(path, session_id):
                    return claude_messages(read_records(path), session_id)
            except (OSError, ValueError):
                continue
        return []


def local_addresses():
    addresses = {"127.0.0.1"}
    try:
        result = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True, timeout=3)
        for line in result.stdout.splitlines():
            words = line.split()
            if len(words) > 1 and words[0] == "inet":
                address = ipaddress.ip_address(words[1])
                if not address.is_link_local and not address.is_unspecified:
                    addresses.add(str(address))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return sorted(addresses)


class WebApplication:
    def __init__(self, native, assets, codex_home, lan=False, *, store=None, reset_auth=False):
        self.native = native
        self.live = None
        self.assets = Path(assets)
        # Keep the HTML reference and CSS bytes in the same release snapshot.
        # A stable content fingerprint avoids reusing Safari's parsed stylesheet
        # from an older release, without inline CSS or client-side reload tricks.
        self.stylesheet = (self.assets / "style.css").read_bytes()
        self.style_hash = hashlib.sha256(self.stylesheet).hexdigest()
        self.stylesheet_url = f"/assets/{self.style_hash}/style.css"
        self.index_html = (self.assets / "index.html").read_bytes().replace(
            b'href="/style.css"', f'href="{self.stylesheet_url}"'.encode())
        self.style_diagnostics_path = Path(store) / "web-style-diagnostics.json" if store else None
        self.style_diagnostics = deque(maxlen=20)
        self.style_diagnostics_lock = threading.Lock()
        self.history = CodexHistory(codex_home)
        self.histories = {str(self.history.home): self.history}
        self.claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
        self.lan = lan
        self.token = secrets.token_urlsafe(32)
        self.push = None
        self.https_origin = ""
        if store:
            auth_file = Path(store) / "web-auth.json"
            if auth_file.exists() and not reset_auth:
                self.token = json.loads(auth_file.read_text())["token"]
            else:
                private_write(auth_file, json.dumps({"token": self.token}).encode())
        self.code = f"{secrets.randbelow(1_000_000):06d}"
        self.code_created = time.monotonic()
        self.pair_attempts = deque()
        self.pair_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.sent = OrderedDict()
        self.launch_lock = threading.Lock()
        self.launched = OrderedDict()
        self.created_directories = OrderedDict()
        self.upgrades = OrderedDict()
        self.closed_windows = OrderedDict()
        self.voice = VoiceService(self.thread)
        backend = CodexBrain(store, lambda: {'sessions': [self.with_runtime(s) for s in self.native.call('list').get('sessions', [])]}, self.native.call,
            lambda session, method, params: self.live.brain_call(session, method, params) if self.live else
                (_ for _ in ()).throw(ValueError('Brain Codex 連線尚未就緒。'))) if store else None
        self.brain = BrainService(store, self.state, self.thread, lambda data: self.send(data, origin="brain"), backend=backend) if store else None
        self.hosts = {"127.0.0.1", "localhost"}
        if lan:
            self.hosts.update(local_addresses())

    def pair(self, code):
        with self.pair_lock:
            now = time.monotonic()
            while self.pair_attempts and now - self.pair_attempts[0] > 60:
                self.pair_attempts.popleft()
            if len(self.pair_attempts) >= 6:
                raise ValueError("嘗試次數過多，請一分鐘後再試。")
            self.pair_attempts.append(now)
            if now - self.code_created > 600:
                raise ValueError("配對碼已過期，請在 Terminal Pilot 重新產生連線。")
            if not isinstance(code, str) or not hmac.compare_digest(code.encode(), self.code.encode()):
                raise ValueError("配對碼不正確。")
            return {"token": self.token}

    def refresh_pairing(self):
        with self.pair_lock:
            self.code = f"{secrets.randbelow(1_000_000):06d}"
            self.code_created = time.monotonic()
            self.pair_attempts.clear()
        return self.code

    def state(self):
        state = self.native.call("list")
        state["sessions"] = [self.with_runtime(s) for s in state.get("sessions", [])]
        if getattr(self, "brain", None) is not None:
            state["notifications"] = self.brain.notification_state(state)["sessions"]
        return {**state, "scope": "lan" if self.lan else "local", "httpsURL": self.https_origin}

    def with_runtime(self, session):
        if session.get("tool") != "codex" or self.live is None:
            return session
        runtime = self.live.snapshot(session)
        result = {**session, "runtime": runtime}
        if runtime["state"] in {"approval", "question"}:
            result.update(phase="waiting", approvalReviewer="user", canSend=False,
                          noticeReason="permission" if runtime["state"] == "approval" else "user_input")
        elif runtime["state"] == "running":
            result.update(phase="running", status="正在處理")
        return result

    def style_health(self, data):
        # Local, bounded diagnostics only. Never accept URLs, credentials, device
        # identifiers or conversation text from the client into this log.
        booleans = {"applied", "sheetLoaded", "standalone", "sheetDisabled", "rulesReadable", "rootRulePresent"}
        numbers = {"duration", "transferSize", "encodedBodySize", "decodedBodySize", "responseStatus", "probeStatus", "probeBytes", "ruleCount"}
        strings = {"probeType", "probeHash", "probeError"}
        if not set(data) <= booleans | numbers | strings:
            raise ValueError("不接受這個診斷欄位。")
        for key, value in data.items():
            if key in booleans and type(value) is not bool:
                raise ValueError("診斷資料格式不正確。")
            if key in numbers and (type(value) not in {int, float} or not 0 <= value <= 10_000_000):
                raise ValueError("診斷資料格式不正確。")
            if key in strings and (not isinstance(value, str) or len(value) > 80 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/;= +-_." for c in value)):
                raise ValueError("診斷資料格式不正確。")
        css = self.stylesheet
        entry = {"time": time.time(), **data, "serverBytes": len(css), "serverHash": hashlib.sha256(css).hexdigest()}
        with self.style_diagnostics_lock:
            self.style_diagnostics.append(entry)
            if self.style_diagnostics_path:
                private_write(self.style_diagnostics_path, json.dumps(list(self.style_diagnostics)).encode())
        return {"recorded": True}

    def push_request(self, route, data):
        if not self.push:
            raise ValueError("請先在 Mac 的 Terminal Pilot 啟用 iPhone HTTPS 通知。")
        if route == "/api/push/config":
            return {"publicKey": self.push.public_key}
        if route == "/api/push/subscribe":
            return self.push.subscribe(data.get("subscription"))
        endpoint = data.get("endpoint")
        if not isinstance(endpoint, str) or len(endpoint) > 4096:
            raise ValueError("缺少通知訂閱。")
        identity = endpoint_id(endpoint)
        if route == "/api/push/status":
            return self.push.status(identity)
        if route == "/api/push/unsubscribe":
            return self.push.unsubscribe(identity)
        if route == "/api/push/test":
            return self.push.test(identity)
        raise ValueError("找不到這個通知操作。")

    def thread(self, session_id):
        sessions = self.native.call("list").get("sessions", [])
        session = next((s for s in sessions if s["id"] == session_id), None)
        if not session:
            raise ValueError("這個工作已不在 Terminal Pilot 的工作清單。")
        session = self.with_runtime(session)
        tool = session.get("tool", "codex")
        if tool == "claude":
            history = ClaudeHistory(session.get("claudeHome") or self.claude_home)
        elif tool == "codex":
            home = str(Path(session.get("codexHome") or self.history.home).resolve())
            history = self.histories.setdefault(home, CodexHistory(home))
        else:
            return {"session": session, "messages": [], "source": "terminal"}
        messages = history.conversation(session)
        if tool == 'codex':
            key = (session['id'], session.get('codexThreadID'), session.get('activeTurn'), session.get('lastTurn'))
            with history.lock: progress = history.activities.get(key, {}).copy()
            session = {**session, 'turnProgress':progress}
        return {"session": session, "messages": messages, "source": tool if messages else "none"}

    def launch(self, data):
        allowed = {"requestID", "projectID", "tool", "directory", "name", "pinned", "mode", "sessionID"}
        if set(data) - allowed:
            raise ValueError("開啟工作不接受自訂指令或額外參數。")
        request_id = data.get("requestID")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 80:
            raise ValueError("缺少開啟識別碼。")
        params = {key: value for key, value in data.items() if key != "requestID"}
        for key, value in params.items():
            if key == "pinned":
                if not isinstance(value, bool):
                    raise ValueError("固定專案格式不正確。")
            elif not isinstance(value, str) or len(value) > 4096:
                raise ValueError("工作設定格式不正確。")
        fingerprint = hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.launch_lock:
            if request_id in self.launched:
                previous, result = self.launched[request_id]
                if previous != fingerprint:
                    raise ValueError("開啟識別碼已用於另一個工作。")
                return result
            self.launched[request_id] = (fingerprint, {"opened": False, "uncertain": True})
            while len(self.launched) > 128:
                self.launched.popitem(last=False)
            result = self.native.call("launch", params)
            self.launched[request_id] = (fingerprint, result)
            return result

    def create_directory(self, data):
        if set(data) != {'parent', 'name', 'requestID'} or any(
                not isinstance(data[k], str) or not data[k] or len(data[k]) > limit
                for k, limit in [('parent', 4096), ('name', 255), ('requestID', 80)]):
            raise ValueError('請選擇上層資料夾並輸入有效名稱。')
        name = data['name'].strip()
        if not name or name in {'.', '..'} or '/' in name or '\\' in name or any(ord(c) < 32 or ord(c) == 127 for c in data['name']):
            raise ValueError('請輸入單一資料夾名稱，不能使用路徑或控制字元。')
        fingerprint = (data['parent'], data['name'])
        with self.launch_lock:
            previous = self.created_directories.get(data['requestID'])
            if previous:
                if previous[0] != fingerprint:
                    raise ValueError('此識別碼已用於另一個資料夾。')
                return previous[1]
            self.created_directories[data['requestID']] = (fingerprint, {'created': False, 'uncertain': True})
            while len(self.created_directories) > 128: self.created_directories.popitem(last=False)
            result = self.native.call('createDirectory', {'parent': data['parent'], 'name': data['name']})
            self.created_directories[data['requestID']] = (fingerprint, result)
            return result

    def send(self, data, *, origin="manager"):
        session_id, request_id, text = data.get("session"), data.get("requestID"), data.get("text")
        if not isinstance(request_id, str) or len(request_id) > 80 or not request_id:
            raise ValueError("缺少送出識別碼。")
        if not isinstance(text, str) or not text.strip() or len(text) > 8_000:
            raise ValueError("請輸入 1 至 8,000 字的回覆。")
        if any(ord(c) < 32 and c not in "\n\t" for c in text) or "\x7f" in text:
            raise ValueError("回覆不能包含終端控制字元。")
        if self.live is not None or origin == "brain":
            session = self.thread(session_id)["session"]
            if session.get("phase") == "waiting":
                raise ValueError("Agent 正在等待核准或選項，請先處理上方的確認卡片。")
            if origin == "brain" and (session.get("phase") in {"running", "starting"} or session.get("turnProgress", {}).get("state") == "active"):
                raise ValueError("工作剛恢復執行，Brain 沒有插入新指令。")
        fingerprint = hashlib.sha256(json.dumps([session_id, data.get("binding"), text], ensure_ascii=False).encode()).hexdigest()
        # Never acquire the Brain lock while holding send_lock: the scheduler
        # takes these in the other order when crossing its dispatch journal.
        if self.brain is not None and origin != "brain":
            self.brain.manual_send(session_id)
        with self.send_lock:
            if request_id in self.sent:
                previous, result = self.sent[request_id]
                if previous != fingerprint:
                    raise ValueError("送出識別碼已用於另一筆回覆。")
                return result
            # Keep ambiguous failures too: never silently repeat terminal input.
            self.sent[request_id] = (fingerprint, {"sent": False, "uncertain": True})
            while len(self.sent) > 128:
                self.sent.popitem(last=False)
            result = self.native.call("send", {"session": session_id, "binding": data.get("binding"), "text": text})
            self.sent[request_id] = (fingerprint, result)
            return result

    def approve(self, data):
        if set(data) - {'session', 'binding', 'approval', 'requestID', 'action', 'content'}:
            raise ValueError('不接受額外的核准欄位。')
        for key in ('session', 'binding', 'approval', 'requestID', 'action'):
            if not isinstance(data.get(key), str) or not 0 < len(data[key]) <= 200:
                raise ValueError('核准識別資料不完整。')
        session = next((s for s in self.native.call('list').get('sessions', []) if s['id'] == data['session']), None)
        if not session or session.get('binding') != data['binding'] or session.get('phase') == 'ended':
            raise ValueError('這個工作已結束或視窗已更換。')
        if session.get('tool') == 'claude':
            if (not data['approval'].startswith('claude-trust:') or data['action'] not in {'accept', 'decline'}
                    or data.get('content') not in (None, {})):
                raise ValueError('Claude 目前只支援資料夾信任確認，不能傳入按鍵或自訂內容。')
            return self.native.call('claudeTrustDecision', {k: data[k] for k in
                ('session', 'binding', 'approval', 'requestID', 'action')})
        if session.get('tool') != 'codex' or self.live is None:
            raise ValueError('核准通道尚未連接。')
        return self.live.decide(session, data)

    def upgrade_approval(self, data):
        if set(data) != {'session', 'binding', 'requestID'} or any(
                not isinstance(value, str) or not 0 < len(value) <= 200 for value in data.values()):
            raise ValueError('續接識別資料不正確。')
        request_id = data['requestID']
        fingerprint = (data['session'], data['binding'])
        with self.launch_lock:
            previous = self.upgrades.get(request_id)
            if previous:
                if previous[0] != fingerprint: raise ValueError('此識別碼已用於另一個工作。')
                return previous[1]
            self.upgrades[request_id] = (fingerprint, {'opened': False, 'uncertain': True})
            while len(self.upgrades) > 128: self.upgrades.popitem(last=False)
            result = self.native.call('approvalUpgrade', {'session': data['session'], 'binding': data['binding']})
            self.upgrades[request_id] = (fingerprint, result)
            return result

    def close_terminal(self, data, *, preview=False):
        allowed = {'session', 'binding'} if preview else {'session', 'binding', 'bindings', 'requestID'}
        if set(data) != allowed or any(not isinstance(data.get(k), str) or not 0 < len(data[k]) <= 200
                                      for k in allowed - {'bindings'}):
            raise ValueError('關閉工作識別資料不正確。')
        if preview: return self.native.call('closePreview', data)
        bindings = data['bindings']
        if (not isinstance(bindings, list) or not 1 <= len(bindings) <= 64 or
                any(not isinstance(b,str) or not 0 < len(b) <= 200 for b in bindings) or len(set(bindings)) != len(bindings)):
            raise ValueError('請重新確認視窗中的工作。')
        fingerprint = (data['session'], data['binding'], tuple(sorted(bindings)))
        with self.launch_lock:
            previous = self.closed_windows.get(data['requestID'])
            if previous:
                if previous[0] != fingerprint: raise ValueError('關閉識別碼已用於另一個視窗。')
                return previous[1]
            self.closed_windows[data['requestID']] = (fingerprint, {'closed':False, 'uncertain':True})
            while len(self.closed_windows) > 128: self.closed_windows.popitem(last=False)
            result = self.native.call('close', {k:v for k,v in data.items() if k != 'requestID'})
            self.closed_windows[data['requestID']] = (fingerprint, result)
            return result


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, application, *, proxy=False):
        self.application = application
        self.proxy = proxy
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "TerminalPilot"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *args):
        pass  # No URLs, text, tokens or conversation content in logs.

    def reply(self, status, value, content_type="application/json; charset=utf-8", *, cache_control="no-store"):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Permissions-Policy", "microphone=(self), camera=()")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def trusted_request(self):
        app = self.server.application
        host = self.headers.get("Host", "")
        expected = {f"{name}:{self.server.server_port}" for name in app.hosts}
        origin_expected = "http://" + host
        secure_proxy = (getattr(self.server, "proxy", False) and self.client_address[0] == "127.0.0.1"
                        and app.https_origin and host == urlsplit(app.https_origin).netloc)
        if secure_proxy:
            origin_expected = app.https_origin
        if host not in expected and not secure_proxy:
            self.reply(403, {"error": "不接受這個連線位址。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != origin_expected:
            self.reply(403, {"error": "不接受跨網站請求。"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.reply(403, {"error": "不接受跨網站請求。"})
            return False
        return True

    def push_transport(self):
        app = self.server.application
        if not (getattr(self.server, "proxy", False) and self.client_address[0] == "127.0.0.1"
                and app.https_origin and self.headers.get("Host") == urlsplit(app.https_origin).netloc):
            raise ValueError("請從 Tailscale HTTPS 網址啟用通知。")

    def authenticated(self):
        expected = "Bearer " + self.server.application.token
        provided = self.headers.get("Authorization", "")
        if not hmac.compare_digest(provided.encode(), expected.encode()):
            self.reply(401, {"error": "請先使用 Terminal Pilot 的配對碼連線。"})
            return False
        return True

    def do_GET(self):
        if not self.trusted_request():
            return
        route = urlsplit(self.path)
        app = self.server.application
        if route.path == app.stylesheet_url:
            self.reply(200, app.stylesheet, "text/css; charset=utf-8",
                       cache_control="public, max-age=31536000, immutable")
            return
        static = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/reading.js": ("reading.js", "text/javascript; charset=utf-8"),
            "/approvals.js": ("approvals.js", "text/javascript; charset=utf-8"),
            "/windows.js": ("windows.js", "text/javascript; charset=utf-8"),
                  "/launch.js": ("launch.js", "text/javascript; charset=utf-8"),
                  "/assistant.js": ("assistant.js", "text/javascript; charset=utf-8"),
                  "/voice.js": ("voice.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/icon.svg": ("icon.svg", "image/svg+xml"),
                  "/pwa.js": ("pwa.js", "text/javascript; charset=utf-8"),
                  "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
                  "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
                  "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
                  "/icon-192.png": ("icon-192.png", "image/png"),
                  "/icon-512.png": ("icon-512.png", "image/png")}
        if route.path in static:
            name, mime = static[route.path]
            app = self.server.application
            body = app.index_html if name == "index.html" else app.stylesheet if name == "style.css" else (app.assets / name).read_bytes()
            self.reply(200, body, mime)
            return
        if not self.authenticated():
            return
        try:
            if route.path == "/api/state":
                result = self.server.application.state()
            elif route.path == "/api/brain":
                if app.brain is None: raise ValueError("Brain 尚未啟動。")
                query = parse_qs(route.query)
                result = app.brain.state(query.get('topic', [''])[0], int(query['before'][0]) if 'before' in query else None)
            elif route.path == '/api/brain/source':
                if app.brain is None: raise ValueError("Brain 尚未啟動。")
                result = app.brain.source(parse_qs(route.query).get('job', [''])[0])
            elif route.path == "/api/thread":
                session_id = parse_qs(route.query).get("session", [""])[0]
                result = self.server.application.thread(session_id)
            elif route.path == "/api/directories":
                directory = parse_qs(route.query).get("directory", ["~"])[0]
                result = self.server.application.native.call("directories", {"directory": directory})
            elif route.path == "/api/voice/status":
                result = self.server.application.voice.status()
            elif route.path == "/api/push/config":
                self.push_transport()
                result = self.server.application.push_request(route.path, {})
            else:
                self.reply(404, {"error": "找不到這個頁面。"})
                return
            self.reply(200, result)
        except (ValueError, OSError):
            self.reply(409, {"error": str(sys.exc_info()[1])})

    def do_POST(self):
        if not self.trusted_request():
            return
        if self.path != "/api/pair" and not self.authenticated():
            return
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("請使用 JSON 送出。")
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("不支援分段請求。")
            length = int(self.headers.get("Content-Length", "0"))
            limit = MAX_VOICE_BODY if self.path == "/api/voice/transcribe" else MAX_BODY
            if not 0 < length <= limit:
                raise ValueError("送出內容過長或為空。")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("請求格式不正確。")
            app = self.server.application
            if self.path == "/api/pair":
                result = app.pair(data.get("code"))
            elif self.path in {"/api/brain/message", "/api/brain/control", "/api/brain/ack", "/api/brain/open", "/api/brain/topics"}:
                if app.brain is None: raise ValueError("Brain 尚未啟動。")
                operation = {"/api/brain/message": app.brain.submit, "/api/brain/control": app.brain.control,
                             "/api/brain/ack": app.brain.acknowledge, "/api/brain/open": app.brain.open_backend,
                             "/api/brain/topics": app.brain.create_topic}[self.path]
                result = operation(data)
            elif self.path == "/api/send":
                result = app.send(data)
            elif self.path == "/api/approval":
                result = app.approve(data)
            elif self.path == "/api/approval/upgrade":
                result = app.upgrade_approval(data)
            elif self.path == "/api/launch":
                result = app.launch(data)
            elif self.path == "/api/directories":
                result = app.create_directory(data)
            elif self.path == "/api/terminal/close-preview":
                result = app.close_terminal(data, preview=True)
            elif self.path == "/api/terminal/close":
                result = app.close_terminal(data)
            elif self.path == "/api/diagnostics/styles":
                result = app.style_health(data)
            elif self.path == "/api/voice/transcribe":
                result = app.voice.transcribe(data)
            elif self.path == "/api/voice/summary":
                result = app.voice.summarize(data)
            elif self.path == "/api/voice/speech":
                result = app.voice.speech(data)
            elif self.path == "/api/voice/demo":
                result = app.voice.demo()
            elif self.path in {"/api/terminal", "/api/ack"}:
                result = app.native.call("read" if self.path == "/api/terminal" else "ack", data)
            elif self.path.startswith("/api/push/"):
                self.push_transport()
                result = app.push_request(self.path, data)
            else:
                self.reply(404, {"error": "找不到這個操作。"})
                return
            self.reply(200, result)
        except (ValueError, OSError, TypeError):
            self.reply(409, {"error": str(sys.exc_info()[1])})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lan", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--tailscale", action="store_true")
    parser.add_argument("--reset-auth", action="store_true")
    args = parser.parse_args()
    native = NativeBridge()
    store = Path(os.environ.get("TERMINAL_PILOT_STORE", str(Path.home() / "Library/Application Support/Terminal Pilot")))
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A restarted child must wait for its predecessor to finish delivery and
    # remove its Serve route before rotating auth or writing subscriptions.
    # Holding this lock for the process lifetime also prevents stale writes after reset.
    service_lock = os.open(store / "web-service.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(service_lock, fcntl.LOCK_EX)
    app = WebApplication(native, Path(__file__).with_name("web"),
                         os.environ.get("CODEX_HOME", str(Path.home() / ".codex")), args.lan,
                         store=store, reset_auth=args.reset_auth)
    # An explicit reset revokes notifications as well as browser authentication.
    if args.reset_auth:
        private_write(store / "push-subscriptions.json", b"{}")
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    try:
        server = WebServer((host, args.port), app)
    except OSError:
        server = WebServer((host, 0), app)
    proxy, https_error = None, ""
    if args.tailscale:
        try:
            from tailscale_web import configure
            # A dedicated loopback socket cannot accidentally proxy a different
            # app bound to the old LAN port, and only this listener trusts HTTPS Host.
            proxy = WebServer(("127.0.0.1", 0), app, proxy=True)
            app.https_origin = configure(store, proxy.server_port)
            app.push = PushService(store, lambda: app.brain.notification_state(app.native.call("list")), app.https_origin)
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            threading.Thread(target=app.push.run, daemon=True).start()
        except (ValueError, OSError) as error:
            https_error = str(error)
            if proxy:
                proxy.server_close()
            proxy = None
            app.https_origin = ""
    def refresh_pairing():
        native.emit({"event": "pairing", "pairingCode": app.refresh_pairing()})
    threading.Thread(target=native.read, args=(server.shutdown, refresh_pairing), daemon=True).start()
    from runtime_paths import find_tool
    runtime_diagnostics = deque(maxlen=20)
    runtime_outcomes = {}
    def runtime_diagnostic(entry):
        identity = (entry['stage'], entry['outcome'], entry['code'])
        if runtime_outcomes.get(entry['session']) == identity: return
        runtime_outcomes[entry['session']] = identity
        if len(runtime_outcomes) > 128: runtime_outcomes.pop(next(iter(runtime_outcomes)))
        runtime_diagnostics.append(entry)
        try: private_write(Path(store) / 'codex-runtime-diagnostics.json', json.dumps(list(runtime_diagnostics)).encode())
        except OSError: pass  # Diagnostics never disable the user's connection.
    app.live = CodexLiveStatus(find_tool("codex"), app.history.home, lambda: native.call("list"),
                              lambda session, status: observe_codex_runtime(store, session, status),
                              bind=lambda session, thread: bind_codex_runtime(store, session, thread),
                              lifecycle=lambda session, event: codex_runtime_event(store, session, event),
                              diagnostic=runtime_diagnostic)
    threading.Thread(target=app.live.run, daemon=True).start()
    app.brain.start()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    native.emit({"event": "ready", "port": server.server_port, "token": app.token, "pairingCode": app.code,
                 "addresses": sorted(app.hosts - {"localhost"}), "lan": args.lan,
                 "httpsURL": app.https_origin, "httpsError": https_error})
    server.serve_forever(poll_interval=0.2)
    server.server_close()
    app.live.stop_event.set()
    app.brain.stop()
    if app.push:
        app.push.stop_event.set()
    if proxy:
        from tailscale_web import stop
        stop(store, proxy.server_port)
        proxy.shutdown()
        proxy.server_close()
    os.close(service_lock)


if __name__ == "__main__":
    main()
