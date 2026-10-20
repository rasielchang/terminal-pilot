import concurrent.futures
from contextlib import closing
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("web_server", ROOT / "scripts/web_server.py")
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class FakeNative:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.sessions = [{"id": "pilot-1", "binding": "123:1:tty1", "directory": "/same/project"}]

    def call(self, method, params=None):
        self.calls.append((method, params))
        if self.fail:
            raise ValueError("native disconnected")
        if method == "launch":
            return {"opened": True, "binding": "new:1:tty2", "tool": params.get("tool", "codex")}
        return {"sessions": self.sessions} if method == "list" else {"sent": True}


class FakeSocket:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = io.BytesIO()

    def makefile(self, *args):
        return self.request

    def settimeout(self, value):
        pass

    def sendall(self, data):
        self.response.write(data)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.native = FakeNative()
        self.app = web.WebApplication(self.native, ROOT / "web", self.home)
        self.server = type("Server", (), {"application": self.app, "server_port": 8765})()

    def tearDown(self):
        if self.app.brain is not None: self.app.brain.close()
        self.doCleanups()  # Close per-test SQLite connections before Windows removes files.
        self.temp.cleanup()

    def request(self, path="/api/state", data=None, *, auth=True, headers=None):
        values = {"Host": "127.0.0.1:8765"}
        if auth:
            values["Authorization"] = "Bearer " + self.app.token
        body = b""
        if data is not None:
            body = json.dumps(data).encode()
            values.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
        values.update(headers or {})
        request = (f"{'POST' if data is not None else 'GET'} {path} HTTP/1.1\r\n" +
                   "".join(f"{k}: {v}\r\n" for k, v in values.items()) + "\r\n").encode() + body
        socket = FakeSocket(request)
        web.Handler(socket, ("127.0.0.1", 1), self.server)
        head, body = socket.response.getvalue().split(b"\r\n\r\n", 1)
        return int(head.split()[1]), head.decode(), body

    def payload(self, **overrides):
        return {"session": "pilot-1", "binding": "123:1:tty1", "text": "第一行\n第二行 `literal` $(literal)",
                "requestID": "request-1", **overrides}

    def test_brain_contract_auth_durable_acceptance_and_control(self):
        self.app.brain = web.BrainService(self.home, self.app.state, self.app.thread, self.app.send,
                                          planner=lambda _: {}, memory_root=self.home / 'empty')
        self.addCleanup(self.app.brain.close)
        self.assertEqual(self.request('/api/brain', auth=False)[0], 401)
        body = {'requestID':'brain-message-1','text':'先回報，不要派送'}
        status, _, response = self.request('/api/brain/message', body)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(response)['accepted'])
        self.assertEqual(self.request('/api/brain/message', body)[2], response)
        self.assertEqual(self.request('/api/brain/message', {**body,'text':'different'})[0], 409)
        self.assertEqual(self.request('/api/brain/message', {**body,'command':'shell'})[0], 409)
        self.assertEqual(self.request('/api/brain/message', body, headers={'Origin':'https://attacker.invalid'})[0], 403)
        self.assertEqual(self.request('/api/brain/control', {'requestID':'pause-brain','action':'pause'})[0], 200)
        state = json.loads(self.request('/api/brain')[2])
        self.assertTrue(state['paused']); self.assertEqual(len(state['messages']), 2)
        self.assertEqual(self.request('/api/brain/ack', {'through':1})[0], 200)
        self.assertFalse(any(method == 'send' for method, _ in self.native.calls))

    def test_brain_topic_routes_pairing_scope_pagination_and_receipts(self):
        self.app.brain = web.BrainService(self.home, self.app.state, self.app.thread, self.app.send,
                                        planner=lambda _: {}, memory_root=self.home / 'empty')
        self.addCleanup(self.app.brain.close)
        data = {'requestID':'topic-create', 'title':'手機斷線'}
        self.assertEqual(self.request('/api/brain/topics', data, auth=False)[0], 401)
        self.assertEqual(self.request('/api/brain/topics', data, headers={'Origin':'https://other.invalid'})[0], 403)
        status, _, body = self.request('/api/brain/topics', data)
        self.assertEqual(status,200); identity=json.loads(body)['topic']['id']
        self.assertEqual(self.request('/api/brain/topics', data)[2],body)
        self.assertEqual(self.request('/api/brain/topics', {**data,'title':'other'})[0],409)
        status, _, body=self.request('/api/brain/message', {'requestID':'topic-send','topic':identity,'text':'先查明，不修改'})
        self.assertEqual(status,200)
        result=json.loads(self.request('/api/brain?topic='+identity)[2])
        self.assertEqual(len(result['messages']),1); self.assertEqual(result['topic']['id'],identity)
        self.assertEqual(json.loads(self.request('/api/brain')[2])['messages'],[])
        self.assertEqual(json.loads(self.request('/api/brain?topic='+identity+'&before=1')[2])['messages'],[])
        self.assertEqual(self.request('/api/brain?topic=missing')[0],409)
        self.assertEqual(self.request('/api/brain?before=bad')[0],409)
        self.assertEqual(self.request('/api/brain/ack', {'topic':identity,'through':999})[0],200)
        self.assertEqual(self.request('/api/brain/source?job=missing')[0],409)
        self.assertFalse(any(method in {'send','brainOpen'} for method,_ in self.native.calls))

    def test_brain_open_uses_existing_pairing_and_no_custom_launch_fields(self):
        class Backend:
            def __init__(self): self.opens=0
            def open(self):
                self.opens+=1
                return {'opened':True,'session':'brain-session','binding':'brain-binding'}
        backend=Backend()
        self.app.brain=web.BrainService(self.home,self.app.state,self.app.thread,self.app.send,backend=backend)
        self.addCleanup(self.app.brain.close)
        body={'requestID':'open-brain'}
        self.assertEqual(self.request('/api/brain/open',body,auth=False)[0],401)
        self.assertEqual(self.request('/api/brain/open',body,headers={'Origin':'https://attacker.invalid'})[0],403)
        self.assertEqual(self.request('/api/brain/open',{**body,'threadID':'other'})[0],409)
        self.assertEqual(self.request('/api/brain/open',body)[0],200)
        self.assertEqual(self.request('/api/brain/open',body)[0],200)
        self.assertEqual(backend.opens,1)

    def test_turn_progress_uses_formal_start_and_ignores_steering_and_other_turns(self):
        def event(kind, turn, timestamp='2026-10-17T00:00:00Z'):
            return {'type':'event_msg','timestamp':timestamp,'payload':{'type':kind,'turn_id':turn}}
        records=[event('task_started','active'),event('user_message','active','2026-10-17T00:08:00Z'),
                 event('task_complete','old','2026-10-17T00:09:00Z')]
        progress=web.progress_from_records(records)
        self.assertEqual(progress,{'state':'active','turnID':'active','startedAt':1792195200.0})
        self.assertEqual(web.progress_from_records(records+[event('task_complete','active')])['state'],'complete')
        self.assertEqual(web.progress_from_records(records+[event('turn_aborted','active')])['state'],'interrupted')
        self.assertEqual(web.progress_from_records([event('task_started','active','invalid')]),{})

    def test_long_turn_clock_survives_chat_tail_partial_writes_and_rotation(self):
        path = self.home / 'long.jsonl'
        def event(kind, turn):
            return json.dumps({'type':'event_msg','timestamp':'2026-10-17T00:00:00Z',
                               'payload':{'type':kind,'turn_id':turn}}).encode() + b'\n'
        start = event('task_started','active')
        with path.open('wb') as stream:
            stream.write(start)
            stream.write(b'{"payload":"' + b'x' * (web.MAX_TRANSCRIPT + 100) + b'"}\n')
            stream.write(event('user_message','active'))
        self.assertEqual(web.progress_from_records(web.read_records(path)), {})
        cursor = web.read_progress(path)
        self.assertEqual(cursor['progress']['startedAt'],1792195200.0)
        self.assertEqual(cursor['progress']['state'],'active')
        self.assertEqual(web.read_progress(path,cursor),cursor)
        completion = event('task_complete','active')
        with path.open('ab') as stream: stream.write(completion[:40])
        partial = web.read_progress(path,cursor)
        self.assertEqual(partial,cursor)
        with path.open('ab') as stream: stream.write(completion[40:])
        complete = web.read_progress(path,partial)
        self.assertEqual(complete['progress']['state'],'complete')
        path.write_bytes(event('task_started','next'))
        self.assertEqual(web.read_progress(path,complete)['progress']['turnID'],'next')

    def test_close_window_requires_auth_exact_review_and_idempotent_request(self):
        target = {'session':'pilot-1','binding':'123:1:tty1'}
        data = {**target,'bindings':['123:1:tty1'],'requestID':'close-once'}
        self.assertEqual(self.request('/api/terminal/close',data,auth=False)[0],401)
        self.assertEqual(self.request('/api/terminal/close',data,headers={'Origin':'https://untrusted.example'})[0],403)
        self.assertEqual(self.request('/api/terminal/close-preview',target)[0],200)
        self.assertEqual(self.native.calls[-1],('closePreview',target))
        for invalid in ({**data,'pid':1},{**data,'bindings':[]},{**data,'bindings':['a','a']},{**data,'bindings':'all'}):
            self.assertEqual(self.request('/api/terminal/close',invalid)[0],409)
        self.assertEqual(self.request('/api/terminal/close',data)[0],200)
        self.assertEqual(self.request('/api/terminal/close',data)[0],200)
        self.assertEqual(len([c for c in self.native.calls if c[0]=='close']),1)
        self.assertEqual(self.request('/api/terminal/close',{**data,'binding':'other'})[0],409)
        self.native.fail=True
        with self.assertRaises(ValueError): self.app.close_terminal({**data,'requestID':'failed'})
        self.native.fail=False
        self.assertTrue(self.app.close_terminal({**data,'requestID':'failed'})['uncertain'])

    def test_approval_endpoint_auth_origin_and_exact_live_target(self):
        calls = []
        class Live:
            def decide(_, session, data):
                calls.append((session.copy(), data.copy()))
                return {'submitted':True,'resolved':True,'uncertain':False}
        self.app.live = Live()
        self.native.sessions[0].update(tool='codex', phase='waiting')
        payload = {'session':'pilot-1','binding':'123:1:tty1','approval':'opaque-handle',
                   'requestID':'choice-1','action':'decline','content':None}
        self.assertEqual(self.request('/api/approval', payload, auth=False)[0],401)
        self.assertEqual(self.request('/api/approval', payload, headers={'Origin':'https://evil.test'})[0],403)
        self.assertEqual(calls, [])
        code,_,body = self.request('/api/approval',payload)
        self.assertEqual(code,200);self.assertTrue(json.loads(body)['resolved'])
        self.assertEqual(calls[0][0]['id'],'pilot-1')
        self.assertEqual(calls[0][1],payload)
        self.assertFalse(any(c[0]=='send' for c in self.native.calls))
        self.assertEqual(self.request('/api/approval',{**payload,'rpcID':12})[0],409)
        self.native.sessions.clear()
        self.assertEqual(self.request('/api/approval',payload)[0],409)

    def test_claude_workspace_trust_uses_only_the_native_scoped_decision(self):
        self.native.sessions[0].update(tool='claude', phase='connected', runtime={'state': 'approval', 'approvals': [
            {'id': 'claude-trust:opaque', 'kind': 'workspace_trust', 'state': 'pending'}]})
        payload = {'session': 'pilot-1', 'binding': '123:1:tty1', 'approval': 'claude-trust:opaque',
                   'requestID': 'trust-1', 'action': 'accept', 'content': {}}
        self.assertEqual(self.request('/api/approval', payload, auth=False)[0], 401)
        self.assertEqual(self.request('/api/approval', payload, headers={'Origin':'https://evil.test'})[0], 403)
        for invalid in ({'action': 'cancel'}, {'content': {'keys': 'yes'}}, {'approval': 'codex-request'},
                        {'binding': 'other'}, {'directory': '/other'}):
            self.assertEqual(self.request('/api/approval', {**payload, **invalid})[0], 409)
        self.assertFalse(any(c[0] == 'claudeTrustDecision' for c in self.native.calls))
        self.assertEqual(self.request('/api/approval', payload)[0], 200)
        call = next(c for c in self.native.calls if c[0] == 'claudeTrustDecision')
        self.assertEqual(call[1], {k: v for k, v in payload.items() if k != 'content'})
        self.assertFalse(any(c[0] == 'send' for c in self.native.calls))
        code, _, body = self.request('/api/thread?session=pilot-1')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['session']['runtime']['approvals'][0]['kind'], 'workspace_trust')

    def test_directory_creation_contract_and_duplicate_delivery(self):
        calls = []
        def create(method, params=None):
            calls.append((method, params))
            return {'created': True, 'directory': '/projects/new'}
        self.native.call = create
        data = {'parent': '/projects', 'name': 'new', 'requestID': 'mkdir-1'}
        self.assertEqual(self.request('/api/directories', data, auth=False)[0], 401)
        self.assertEqual(self.request('/api/directories', data, headers={'Origin':'https://evil.test'})[0], 403)
        for invalid in ({'name': '../escape'}, {'name': '.'}, {'name': 'a/b'}, {'name': 'a\\b'}, {'name': 'bad\nname'}, {'command': 'mkdir'}, {'requestID': ''}):
            self.assertEqual(self.request('/api/directories', {**data, **invalid})[0], 409)
        self.assertEqual(calls, [])
        for _ in range(2):
            code, _, body = self.request('/api/directories', data)
            self.assertEqual(code, 200); self.assertTrue(json.loads(body)['created'])
        self.assertEqual(calls, [('createDirectory', {'parent': '/projects', 'name': 'new'})])
        self.assertEqual(self.request('/api/directories', {**data, 'parent': '/other'})[0], 409)
        def lost(method, params=None):
            calls.append((method, params)); raise ValueError('Lost response')
        self.native.call = lost
        with self.assertRaises(ValueError): self.app.create_directory({**data, 'requestID': 'mkdir-2'})
        self.assertTrue(self.app.create_directory({**data, 'requestID': 'mkdir-2'})['uncertain'])
        self.assertEqual(len(calls), 2, 'Uncertain creation must not repeat native writes')

    def test_upgrade_is_target_bound_and_never_retries_ambiguous_native_call(self):
        calls=[]
        def native(method,params=None):
            calls.append((method,params));raise ValueError('Lost response')
        self.native.call=native
        payload={'session':'old','binding':'tty-old','requestID':'migration-1'}
        self.assertEqual(self.request('/api/approval/upgrade',payload)[0],409)
        code,_,body=self.request('/api/approval/upgrade',payload)
        self.assertEqual(code,200);self.assertTrue(json.loads(body)['uncertain'])
        self.assertEqual(len(calls),1)
        self.assertEqual(self.request('/api/approval/upgrade',{**payload,'binding':'other'})[0],409)
        self.assertEqual(self.request('/api/approval/upgrade',{**payload,'threadId':'other'})[0],409)

    def test_http_requires_token_and_blocks_cross_site_and_rebinding(self):
        self.assertEqual(self.request(auth=False)[0], 401)
        for headers in [{"Host": "evil.example:8765"}, {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}]:
            self.assertEqual(self.request(headers=headers)[0], 403)
        status, headers, body = self.request(headers={"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["sessions"]), 1)
        self.assertIn("Cache-Control: no-store", headers)
        self.assertIn("frame-ancestors 'none'", headers)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_static_assets_are_local_and_no_path_traversal(self):
        for path in ["/", "/app.js", "/reading.js", "/launch.js", "/assistant.js", "/voice.js", "/style.css", "/icon.svg", "/pwa.js", "/sw.js", "/manifest.webmanifest", "/apple-touch-icon.png"]:
            self.assertEqual(self.request(path, auth=False)[0], 200)
        self.assertEqual(self.request("/../web_server.py")[0], 404)

    def test_stylesheet_is_external_complete_css_with_strict_csp(self):
        status, headers, body = self.request("/", auth=False)
        self.assertEqual(status, 200)
        self.assertIn(f'<link rel="stylesheet" href="{self.app.stylesheet_url}">'.encode(), body)
        self.assertNotIn(b"<style", body)
        self.assertIn("style-src 'self';", headers)
        status, headers, css = self.request("/style.css", auth=False)
        self.assertEqual(status, 200)
        self.assertIn("Content-Type: text/css; charset=utf-8", headers)
        self.assertIn(f"Content-Length: {len(css)}", headers)
        self.assertEqual(css, (ROOT / "web/style.css").read_bytes())
        status, headers, versioned = self.request(self.app.stylesheet_url, auth=False)
        self.assertEqual(status,200)
        self.assertEqual(versioned,css)
        self.assertIn('Cache-Control: public, max-age=31536000, immutable',headers)
        self.assertIn('Cache-Control: no-store',self.request('/',auth=False)[1])
        self.assertIn('Cache-Control: no-store',self.request('/api/state')[1])
        self.assertEqual(self.request('/assets/wrong/style.css')[0],404)

    def test_stylesheet_release_changes_url_and_preserves_matching_snapshot(self):
        assets = self.home / 'assets'
        assets.mkdir()
        (assets / 'index.html').write_bytes((ROOT / 'web/index.html').read_bytes())
        css = (ROOT / 'web/style.css').read_bytes()
        (assets / 'style.css').write_bytes(css)
        old = web.WebApplication(self.native, assets, self.home)
        (assets / 'style.css').write_bytes(css + b'\n/* next release */\n')
        new = web.WebApplication(self.native, assets, self.home)
        self.assertNotEqual(old.style_hash, new.style_hash)
        self.assertEqual(old.stylesheet, css)
        for app in (old, new):
            self.assertIn(app.style_hash.encode(), app.index_html)
            self.assertEqual(hashlib.sha256(app.stylesheet).hexdigest(), app.style_hash)
        self.assertEqual(new.index_html, web.WebApplication(self.native, assets, self.home).index_html)

    def test_stylesheet_diagnostics_are_authenticated_bounded_and_content_free(self):
        data = {"applied": False, "sheetLoaded": False, "standalone": True, "probeStatus": 200, "probeBytes": 17270, "probeType": "text/css; charset=utf-8"}
        self.assertEqual(self.request("/api/diagnostics/styles", data, auth=False)[0], 401)
        self.assertEqual(self.request("/api/diagnostics/styles", data, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/api/diagnostics/styles", data)[0], 200)
        entry = self.app.style_diagnostics[-1]
        self.assertEqual(entry["serverHash"], hashlib.sha256((ROOT / "web/style.css").read_bytes()).hexdigest())
        for invalid in [{"token": "private"}, {"url": "private"}, {"probeError": "<private>"}, {"applied": "yes"}, {"duration": float("nan")}]:
            self.assertEqual(self.request("/api/diagnostics/styles", invalid)[0], 409)
        for _ in range(25):
            self.app.style_health(data)
        self.assertEqual(len(self.app.style_diagnostics), 20)


    def test_voice_routes_share_auth_and_origin_guards_and_larger_upload_is_scoped(self):
        with patch.object(self.app, "voice") as voice:
            voice.status.return_value = {"available": True}
            voice.transcribe.return_value = voice.summarize.return_value = voice.speech.return_value = voice.demo.return_value = {"text": "test"}
            self.assertEqual(self.request("/api/voice/status", auth=False)[0], 401)
            self.assertEqual(self.request("/api/voice/status")[0], 200)
            for route in ["transcribe", "summary", "speech", "demo"]:
                self.assertEqual(self.request("/api/voice/" + route, {}, auth=False)[0], 401)
                self.assertEqual(self.request("/api/voice/" + route, {}, headers={"Origin": "https://evil.example"})[0], 403)
                self.assertEqual(self.request("/api/voice/" + route, {})[0], 200)
            large = {"audio": "a" * 50000}
            self.assertEqual(self.request("/api/voice/transcribe", large)[0], 200)
            self.assertEqual(self.request("/api/voice/summary", large)[0], 409)
            self.assertEqual(self.request("/api/send", large)[0], 409)
            self.assertEqual(self.request("/api/voice/transcribe", {}, headers={"Content-Length": str(web.MAX_VOICE_BODY + 1)})[0], 409)
        _, headers, _ = self.request("/")
        self.assertIn("media-src 'self' blob:", headers)
        self.assertIn("microphone=(self)", headers)

    def test_https_proxy_accepts_exact_configured_origin_only_on_private_listener(self):
        self.app.https_origin = "https://mac.example.ts.net:8443"
        headers = {"Host": "mac.example.ts.net:8443", "Origin": self.app.https_origin}
        self.assertEqual(self.request(headers=headers)[0], 403)
        self.server.proxy = True
        self.assertEqual(self.request(headers=headers)[0], 200)
        self.assertEqual(self.request(headers={**headers, "Origin": "http://mac.example.ts.net:8443"})[0], 403)
        self.assertEqual(self.request("/api/push/config", auth=False, headers=headers)[0], 401)
        self.assertEqual(self.request("/api/push/config")[0], 409)
        self.assertEqual(self.request("/api/push/subscribe", {"subscription": {}}, auth=False, headers=headers)[0], 401)

    def test_auth_persists_normal_restart_and_explicit_reset_revokes(self):
        first = web.WebApplication(self.native, ROOT / "web", self.home, store=self.home)
        restart = web.WebApplication(self.native, ROOT / "web", self.home, store=self.home)
        reset = web.WebApplication(self.native, ROOT / "web", self.home, store=self.home, reset_auth=True)
        for app in (first, restart, reset): self.addCleanup(app.brain.close)
        self.assertEqual(first.token, restart.token)
        self.assertNotEqual(first.token, reset.token)

    def test_refresh_pairing_keeps_authentication_and_push(self):
        token = self.app.token
        service = self.app.push = object()
        self.app.code_created -= 601
        self.app.refresh_pairing()
        self.assertEqual(self.app.pair(self.app.code)["token"], token)
        self.assertIs(self.app.push, service)

    def test_pairing_expiry_rate_limit_and_unicode(self):
        self.assertEqual(self.request("/api/pair", {"code": "中文字"}, auth=False)[0], 409)
        response = self.request("/api/pair", {"code": self.app.code}, auth=False)
        self.assertEqual(json.loads(response[2])["token"], self.app.token)
        self.app.code_created -= 601
        self.assertEqual(self.request("/api/pair", {"code": self.app.code}, auth=False)[0], 409)
        self.app.code_created = time.monotonic()
        for _ in range(3):
            self.request("/api/pair", {"code": "wrong"}, auth=False)
        self.assertIn("次數", json.loads(self.request("/api/pair", {"code": self.app.code}, auth=False)[2])["error"])

    def test_body_format_limits_and_control_characters(self):
        for headers in [{"Content-Type": "text/plain"}, {"Transfer-Encoding": "chunked"}, {"Content-Length": "40001"}]:
            self.assertEqual(self.request("/api/send", self.payload(), headers=headers)[0], 409)
        for text in ["", " \n", "x" * 8001, "\x1b[A", "\x00", "\x03", "\x7f"]:
            self.assertEqual(self.request("/api/send", self.payload(text=text))[0], 409)
        self.assertEqual(self.native.calls, [])

    def test_concurrent_retries_deliver_once_and_reject_changed_payload(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.app.send(self.payload()), range(12)))
        self.assertTrue(all(r["sent"] for r in results))
        self.assertEqual(len(self.native.calls), 1)
        self.assertEqual(self.native.calls[0][1]["text"], self.payload()["text"])
        with self.assertRaises(ValueError):
            self.app.send(self.payload(text="another"))

    def test_ambiguous_native_failure_is_not_replayed(self):
        self.native.fail = True
        with self.assertRaises(ValueError):
            self.app.send(self.payload())
        self.native.fail = False
        self.assertEqual(self.app.send(self.payload()), {"sent": False, "uncertain": True})
        self.assertEqual(len(self.native.calls), 1)

    def test_launch_and_directory_routes_require_auth_and_preserve_arguments(self):
        data = {"requestID": "open-1", "tool": "claude", "directory": "/a ' $(literal)",
                "name": "手機專案", "pinned": True, "mode": "specific", "sessionID": "old conversation"}
        self.assertEqual(self.request("/api/launch", data, auth=False)[0], 401)
        self.assertEqual(self.request("/api/directories?directory=%2Ftmp", auth=False)[0], 401)
        self.assertEqual(self.native.calls, [])
        self.assertEqual(self.request("/api/launch", data, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/api/launch", data)[0], 200)
        self.assertEqual(self.native.calls, [("launch", {k: v for k, v in data.items() if k != "requestID"})])
        self.assertEqual(self.request("/api/directories?directory=%2Ftmp%2Fa%20b")[0], 200)
        self.assertEqual(self.native.calls[-1], ("directories", {"directory": "/tmp/a b"}))

    def test_launch_retries_once_and_ambiguous_failure_is_not_replayed(self):
        data = {"requestID": "open-1", "projectID": "saved", "mode": "latest"}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.app.launch(data), range(12)))
        self.assertTrue(all(r["opened"] for r in results))
        self.assertEqual(len(self.native.calls), 1)
        with self.assertRaises(ValueError):
            self.app.launch({**data, "mode": "new"})
        self.native.fail = True
        data["requestID"] = "open-2"
        with self.assertRaises(ValueError):
            self.app.launch(data)
        self.native.fail = False
        self.assertEqual(self.app.launch(data), {"opened": False, "uncertain": True})
        self.assertEqual(len(self.native.calls), 2)

    def test_launch_rejects_custom_commands_and_malformed_fields(self):
        for changes in [{"command": "anything"}, {"args": []}, {"_deadline": 999},
                        {"directory": []}, {"pinned": "true"}, {"requestID": ""}]:
            self.assertEqual(self.request("/api/launch", {"requestID": "open-1", **changes})[0], 409)
        self.assertEqual(self.native.calls, [])

    def rollout(self, name, turn, text, **meta):
        path = self.home / "sessions/2026/10/15" / f"rollout-{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [{"type": "session_meta", "payload": {"id": name, "source": "cli", **meta}},
                   {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn}},
                   {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": turn,
                    "item": {"type": "AgentMessage", "id": name + "-msg", "content": [{"type": "Text", "text": text}]}}}]
        path.write_text("\n".join(map(json.dumps, records)) + "\n")
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("CREATE TABLE IF NOT EXISTS threads(id TEXT, rollout_path TEXT, cwd TEXT, updated_at INT)")
            db.execute("INSERT INTO threads VALUES(?,?,?,?)", (name, str(path), "/same/project", 1))
            db.commit()
        return path

    def test_same_project_isolated_by_exact_turn_or_thread(self):
        self.rollout("thread-a", "turn-a", "ONLY A")
        self.rollout("thread-b", "turn-b", "ONLY B")
        session = self.native.sessions[0]
        self.assertEqual(self.app.history.conversation({**session, "activeTurn": "missing"}), [])
        self.assertEqual(self.app.history.conversation({**session, "activeTurn": "turn-b"})[0]["text"], "ONLY B")
        self.assertEqual(self.app.history.conversation({**session, "codexThreadID": "thread-a"})[0]["text"], "ONLY A")

    def test_subagent_and_external_paths_cannot_leak_into_conversation(self):
        child = self.rollout("child", "turn-a", "PRIVATE CHILD", parent_thread_id="parent")
        session = {**self.native.sessions[0], "codexThreadID": "child"}
        self.assertEqual(self.app.history.conversation(session), [])
        outside = self.home / "outside.jsonl"
        outside.write_bytes(child.read_bytes())
        link = child.with_name("symlink.jsonl")
        try: link.symlink_to(outside)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314: self.skipTest('Windows account lacks symbolic-link privilege')
            raise
        self.assertFalse(self.app.history.allowed(outside))
        self.assertFalse(self.app.history.allowed(link))

    def test_reading_does_not_ack_and_unknown_session_is_rejected(self):
        self.rollout("thread-a", "turn-a", "MESSAGE")
        self.native.sessions[0]["activeTurn"] = "turn-a"
        result = self.app.thread("pilot-1")
        self.assertEqual(result["source"], "codex")
        self.assertTrue(all(call[0] == "list" for call in self.native.calls))
        with self.assertRaises(ValueError):
            self.app.thread("other")

    def test_transcript_partial_tail_dedup_and_no_hidden_context(self):
        path = self.rollout("thread-a", "turn-a", "VISIBLE")
        with path.open("a") as stream:
            stream.write(json.dumps({"type": "response_item", "payload": {"role": "system", "content": "HIDDEN"}}) + "\n")
            stream.write(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "Reasoning", "content": [{"type": "Text", "text": "HIDDEN"}]}}}) + "\n")
            stream.write('{"partial":')
        records = web.read_records(path)
        messages = web.messages_from_records(records + records)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["text"], "VISIBLE")

    def test_old_transcript_format(self):
        messages = web.messages_from_records([
            {"type": "event_msg", "payload": {"type": "user_message", "message": "question"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "answer"}},
        ])
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])

    def test_commentary_is_visible_and_distinguished_from_final_without_reasoning(self):
        records = [{"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": kind, "id": str(index), "phase": phase, "content": [{"type": "Text", "text": text}]}}}
            for index, (kind, phase, text) in enumerate([
                ("AgentMessage", "commentary", "Still working"), ("Reasoning", "analysis", "PRIVATE"),
                ("AgentMessage", "final_answer", "Done")])]
        messages = web.messages_from_records(records)
        self.assertEqual([m["phase"] for m in messages], ["commentary", "final_answer"])
        self.assertNotIn("PRIVATE", json.dumps(messages))

    def test_runtime_approval_disables_send_without_automatically_answering(self):
        self.native.sessions[0].update(tool="codex", phase="complete", canSend=True)
        self.app.live = type("Live", (), {"snapshot": lambda _, s: {"state": "approval"}})()
        session = self.app.state()["sessions"][0]
        self.assertEqual(session["phase"], "waiting")
        self.assertFalse(session["canSend"])
        self.assertEqual(self.request("/api/send", self.payload())[0], 409)
        self.assertTrue(all(method == "list" for method, _ in self.native.calls))

    def test_hook_transcript_path_works_before_database_indexing(self):
        path = self.rollout("fresh", "turn-fresh", "FRESH")
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("DELETE FROM threads")
            db.commit()
        session = {**self.native.sessions[0], "codexThreadID": "fresh", "codexTranscript": str(path)}
        self.assertEqual(self.app.history.conversation(session)[0]["text"], "FRESH")
        # An unindexed file can also be found by exact thread filename, with metadata verified.
        session.pop("codexTranscript")
        self.assertEqual(web.CodexHistory(self.home).conversation(session)[0]["text"], "FRESH")

    def test_custom_codex_home_uses_session_metadata(self):
        self.rollout("custom", "turn-custom", "CUSTOM HOME")
        self.native.sessions[0].update(codexThreadID="custom", codexHome=str(self.home))
        self.app.history = web.CodexHistory(self.home / "wrong-default")
        self.assertEqual(self.app.thread("pilot-1")["messages"][0]["text"], "CUSTOM HOME")

    def test_claude_exact_session_and_visible_text_only(self):
        sid, other = str(uuid.uuid4()), str(uuid.uuid4())
        path = self.home / "projects/same-project" / (sid + ".jsonl")
        path.parent.mkdir(parents=True)
        def record(identity, role, content, **extra):
            return {"sessionId": sid, "uuid": identity, "type": role,
                    "message": {"role": role, "content": content}, **extra}
        records = [record("u", "user", "Question"),
                   record("a", "assistant", [{"type": "thinking", "thinking": "PRIVATE"},
                          {"type": "tool_use", "input": {"private": "PRIVATE"}},
                          {"type": "text", "text": "Answer"}]),
                   record("tool", "user", [{"type": "tool_result", "content": "PRIVATE"}]),
                   record("meta", "user", "PRIVATE", isMeta=True),
                   record("summary", "user", "PRIVATE", isCompactSummary=True),
                   record("child", "assistant", "PRIVATE", isSidechain=True),
                   record("wrong", "assistant", "PRIVATE", sessionId=other),
                   record("local", "user", "<command-name>/help</command-name>")]
        path.write_text("\n".join(map(json.dumps, records + [records[1]])) + '\n{"partial":')
        self.native.sessions[0].update(tool="claude", claudeSessionID=sid, claudeHome=str(self.home), claudeTranscript=str(path))
        result = self.app.thread("pilot-1")
        self.assertEqual(result["source"], "claude")
        self.assertEqual([m["text"] for m in result["messages"]], ["Question", "Answer"])
        self.assertTrue(all(method == "list" for method, _ in self.native.calls))
        self.native.sessions[0]["claudeSessionID"] = other
        self.assertEqual(self.app.thread("pilot-1")["messages"], [])

    def test_claude_never_guesses_project_or_reads_child_external_transcript(self):
        sid = str(uuid.uuid4())
        history = web.ClaudeHistory(self.home)
        self.assertEqual(history.conversation({"directory": "/same/project"}), [])
        main = self.home / "projects/project" / (sid + ".jsonl")
        main.parent.mkdir(parents=True)
        main.write_text(json.dumps({"sessionId": sid, "uuid": "a", "type": "assistant",
                                  "message": {"role": "assistant", "content": "MAIN"}}))
        self.assertEqual(history.conversation({"claudeSessionID": sid})[0]["text"], "MAIN")
        child = main.parent / sid / "subagents" / (sid + ".jsonl")
        child.parent.mkdir(parents=True); child.write_bytes(main.read_bytes())
        outside = self.home / (sid + ".jsonl"); outside.write_bytes(main.read_bytes())
        link = self.home / "projects/link" / (sid + ".jsonl")
        link.parent.mkdir()
        try: link.symlink_to(outside)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314: self.skipTest('Windows account lacks symbolic-link privilege')
            raise
        for path in (child, outside, link):
            self.assertEqual(history.conversation({"claudeSessionID": sid, "claudeTranscript": str(path)}), [])

    def test_plain_terminal_has_no_agent_history(self):
        self.native.sessions[0]["tool"] = "terminal"
        self.assertEqual(self.app.thread("pilot-1")["source"], "terminal")

    def test_legacy_claude_requires_exact_registered_process_identity(self):
        sid = str(uuid.uuid4())
        history = web.ClaudeHistory(self.home)
        path = self.home / "projects/same-project" / (sid + ".jsonl")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"sessionId": sid, "uuid": "m", "type": "assistant",
                                   "message": {"role": "assistant", "content": "EXACT"}}))
        registry = self.home / "sessions/123.json"
        registry.parent.mkdir()
        entry = {"pid": 123, "sessionId": sid, "cwd": "/project", "startedAt": 104000,
                 "kind": "interactive", "entrypoint": "cli"}
        registry.write_text(json.dumps(entry))
        session = {"agentPID": 123, "directory": "/project", "createdAt": 100}
        self.assertEqual(history.conversation(session)[0]["text"], "EXACT")
        for changes in ({"agentPID": 124}, {"directory": "/other"}, {"createdAt": 1}):
            self.assertEqual(history.conversation({**session, **changes}), [])
        registry.write_text(json.dumps({**entry, "kind": "subagent"}))
        self.assertEqual(history.conversation(session), [])

    def test_terminal_and_ack_forward_only_after_authentication(self):
        for route in ["/api/terminal", "/api/ack"]:
            self.assertEqual(self.request(route, {"session": "pilot-1"}, auth=False)[0], 401)
        self.assertEqual(self.native.calls, [])
        self.assertEqual(self.request("/api/ack", {"session": "pilot-1", "noticeID": "old"})[0], 200)
        self.assertEqual(self.native.calls, [("ack", {"session": "pilot-1", "noticeID": "old"})])


if __name__ == "__main__":
    unittest.main()
