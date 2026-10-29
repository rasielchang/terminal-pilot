"""Background-specific lifecycle and decision boundaries; no model calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from brain_server import Application, BackgroundHandler
from brain_stdio import BackgroundBrain
from web_push import PushService, b64, public_bytes
from cryptography.hazmat.primitives.asymmetric import ec
from test_web import FakeSocket
from types import SimpleNamespace


class Proxy:
    closed = False
    def __init__(self, *args):
        self.subscribed, self.calls, self.decisions = set(), [], []
    def call(self, method, args):
        self.calls.append((method, args))
        return {'thread': {'id': args.get('threadId', 'exact-thread')}}
    def approvals(self, thread): return []
    def decide(self, *args): self.decisions.append(args); return {'submitted': True}
    def close(self): self.closed = True


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def backend(self, proxy=None):
        return BackgroundBrain(self.root, proxy or Proxy(), self.root, '測試者')

    def application(self):
        with patch('brain_server.StdioProxy', Proxy):
            app = Application(self.root, 'unused', self.root, self.root, '測試者')
        self.addCleanup(app.backend.close)
        self.addCleanup(app.brain.close)
        return app

    def request(self, app, route, data=None, *, auth=True, origin=None):
        body = json.dumps(data).encode() if data is not None else b''
        headers = {'Host': '127.0.0.1:8765'}
        if auth: headers['Authorization'] = 'Bearer ' + app.token
        if origin: headers['Origin'] = origin
        if data is not None:
            headers.update({'Content-Type': 'application/json', 'Content-Length': str(len(body))})
        request = (f"{'POST' if data is not None else 'GET'} {route} HTTP/1.1\r\n" +
                   ''.join(f'{key}: {value}\r\n' for key, value in headers.items()) + '\r\n').encode() + body
        socket = FakeSocket(request)
        BackgroundHandler(socket, ('127.0.0.1', 1), SimpleNamespace(application=app, server_port=8765))
        head, body = socket.response.getvalue().split(b'\r\n\r\n', 1)
        return int(head.split()[1]), json.loads(body)

    def test_http_ack_is_durable_scoped_read_receipt_never_an_approval(self):
        app = self.application(); app.backend.open()
        approvals = [{'id': 'approval-one', 'kind': 'permission', 'state': 'pending'}]
        app.backend.proxy.approvals = lambda _: approvals
        original = app.state()['sessions'][0]
        data = {'session': original['id'], 'noticeID': original['noticeID']}
        self.assertTrue(original['unread'])
        self.assertEqual(self.request(app, '/api/ack', data, auth=False)[0], 401)
        self.assertEqual(self.request(app, '/api/ack', data, origin='https://evil.invalid')[0], 403)
        self.assertTrue(app.state()['sessions'][0]['unread'])
        self.assertEqual(self.request(app, '/api/ack', data), (200, {'acknowledged': True}))
        self.assertEqual(self.request(app, '/api/ack', data), (200, {'acknowledged': True}))
        current = app.state()['sessions'][0]
        self.assertFalse(current['unread'])
        self.assertEqual(current['phase'], 'waiting')
        self.assertEqual(current['runtime']['approvals'], approvals)
        self.assertEqual(app.backend.proxy.decisions, [])
        proxy = Proxy(); proxy.approvals = lambda _: approvals
        resumed = self.backend(proxy); resumed.open()
        self.assertFalse(resumed.state()['sessions'][0]['unread'], 'Restart must retain the read receipt')
        approvals.append({'id': 'approval-two', 'state': 'pending'})
        self.assertTrue(resumed.state()['sessions'][0]['unread'])
        self.assertEqual(self.request(app, '/api/ack', data), (200, {'acknowledged': False}))
        self.assertTrue(app.state()['sessions'][0]['unread'], 'A stale read cannot clear a new permission')
        for invalid in ({'session': 'other', 'noticeID': data['noticeID']}, {**data, 'action': 'accept'},
                        {**data, 'noticeID': None}, {**data, 'session': ''}):
            self.assertEqual(self.request(app, '/api/ack', invalid)[0], 409)
        self.assertEqual(app.backend.proxy.decisions, [])

    def test_ack_storage_failure_keeps_attention_and_returns_error(self):
        app = self.application(); app.backend.open()
        app.backend.proxy.approvals = lambda _: [{'id': 'approval-one', 'state': 'pending'}]
        current = app.state()['sessions'][0]
        with patch.object(app.backend, '_save', side_effect=OSError('fixture storage unavailable')):
            status, result = self.request(app, '/api/ack', {'session': current['id'], 'noticeID': current['noticeID']})
        self.assertEqual(status, 409)
        self.assertIn('error', result)
        self.assertTrue(app.state()['sessions'][0]['unread'])
        self.assertEqual(app.backend.proxy.decisions, [])

    def test_http_unsupported_capabilities_return_errors_without_side_effects(self):
        app = self.application()
        for route, data in [('/api/directories', None), ('/api/directories', {}),
                            ('/api/terminal', {}), ('/api/terminal/input', {}),
                            ('/api/terminal/close-preview', {}), ('/api/terminal/close', {}),
                            ('/api/launch', {}), ('/api/send', {}), ('/api/approval/upgrade', {})]:
            with self.subTest(route=route, method='GET' if data is None else 'POST'):
                self.assertEqual(self.request(app, route, data, auth=False)[0], 401)
                status, response = self.request(app, route, data)
                self.assertEqual(status, 501)
                self.assertEqual(response['code'], 'unsupported_operation')
                self.assertTrue(response['error'])
        self.assertEqual(app.backend.proxy.calls, [])
        self.assertEqual(app.backend.proxy.decisions, [])

    def test_open_persists_exact_thread_without_starting_inference(self):
        proxy = Proxy(); backend = self.backend(proxy)
        backend.open()
        self.assertEqual(json.loads(backend.path.read_text(encoding='utf-8'))['thread'], 'exact-thread')
        self.assertEqual([m for m, _ in proxy.calls], ['thread/start'])
        self.assertEqual(proxy.calls[0][1]['sandbox'], 'read-only')
        self.assertEqual(proxy.calls[0][1]['approvalsReviewer'], 'user')

    def test_restart_resumes_saved_thread_and_rotates_binding(self):
        backend = self.backend(); first = backend.open()
        proxy = Proxy(); restarted = self.backend(proxy); second = restarted.open()
        self.assertEqual([m for m, _ in proxy.calls], ['thread/resume'])
        self.assertEqual(proxy.calls[0][1]['threadId'], 'exact-thread')
        self.assertNotEqual(first['binding'], second['binding'])

    def test_resume_failure_never_creates_replacement_thread(self):
        self.backend().open()
        proxy = Proxy()
        def fail(method, params):
            self.assertEqual(method, 'thread/resume'); raise ValueError('unavailable')
        proxy.call = fail
        with self.assertRaises(ValueError): self.backend(proxy).open()

    def test_saved_session_is_listed_after_restart_without_launching(self):
        self.backend().open()
        proxy = Proxy(); restarted = self.backend(proxy)
        session = restarted.state()['sessions'][0]
        self.assertEqual(session['codexThreadID'], 'exact-thread')
        self.assertEqual(session['id'], restarted.session_id)
        self.assertFalse(session['alive'])
        self.assertEqual(proxy.calls, [])

    def test_raw_reader_keeps_user_and_agent_messages_on_exact_thread(self):
        with patch('brain_server.StdioProxy', Proxy):
            app = Application(self.root, 'unused', self.root, self.root, '測試者')
        self.addCleanup(app.brain.close)
        app.backend.open()
        def call(method, params):
            self.assertEqual(method, 'thread/turns/list')
            self.assertEqual(params['threadId'], 'exact-thread')
            return {'data': [{'id': 'turn-1', 'items': [
                {'id': 'user-1', 'type': 'userMessage', 'content': [{'type': 'text', 'text': '問題'}]},
                {'id': 'agent-1', 'type': 'agentMessage', 'phase': 'final_answer', 'text': '回答'}]}]}
        app.backend.proxy.call = call
        result = app.thread(app.backend.session_id)
        self.assertEqual([m['role'] for m in result['messages']], ['user', 'assistant'])
        self.assertEqual([m['text'] for m in result['messages']], ['問題', '回答'])
        self.assertTrue(result['session']['readOnly'])
        with self.assertRaises(ValueError): app.thread('another-session')

    def test_web_decision_rejects_old_binding_before_forwarding(self):
        with patch('brain_server.StdioProxy', Proxy):
            app = Application(self.root, 'unused', self.root, self.root, '測試者')
        self.addCleanup(app.brain.close)
        app.backend.open()
        decision = {'session': app.backend.session_id, 'binding': 'old-binding',
                    'approval': 'handle', 'requestID': 'decision-1', 'action': 'accept', 'content': None}
        with self.assertRaises(ValueError): app.approve(decision)
        self.assertEqual(app.backend.proxy.decisions, [])
        decision['binding'] = app.backend.current['binding']
        self.assertTrue(app.approve(decision)['submitted'])

    def test_pairing_expires_without_resetting_persistent_auth(self):
        with patch('brain_server.StdioProxy', Proxy):
            app = Application(self.root, 'unused', self.root, self.root, '測試者')
        self.addCleanup(app.brain.close)
        token = app.pair(app.code)['token']
        app.code_created -= 601
        with self.assertRaises(ValueError): app.pair(app.code)
        self.assertEqual(app.token, token)

    def test_background_result_reaches_push_and_ack_clears_it(self):
        with patch('brain_server.StdioProxy', Proxy):
            app = Application(self.root, 'unused', self.root, self.root, '測試者')
        self.addCleanup(app.brain.close)
        sent = []
        app.push = PushService(self.root, lambda: app.brain.notification_state(app.backend.state()),
                               'https://pilot.example.ts.net:8443', settle_seconds=0,
                               sender=lambda *args: sent.append(args[1]) or 201)
        key = ec.generate_private_key(ec.SECP256R1())
        sub = {'endpoint': 'https://web.push.apple.com/synthetic-test',
               'keys': {'p256dh': b64(public_bytes(key)), 'auth': b64(b'a' * 16)}}
        self.assertTrue(app.push_request('/api/push/subscribe', {'subscription': sub})['subscribed'])
        with app.brain.lock, app.brain.db:
            identity = app.brain._message('合成結果', kind='result')
        self.assertTrue(next(s for s in app.state()['notifications'] if s['id'] == 'brain')['unread'])
        app.push.tick(); app.push.tick()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['session'], 'brain')
        self.assertNotIn('合成結果', json.dumps(sent[0], ensure_ascii=False))
        app.brain.acknowledge({'through': identity})
        self.assertFalse(next(s for s in app.state()['notifications'] if s['id'] == 'brain')['unread'])
        app.push.tick(); self.assertEqual(len(sent), 1)
        restarted = PushService(self.root, app.push.state, app.push.subject, sender=app.push.sender, settle_seconds=0)
        self.assertEqual(app.push_request('/api/push/config', {})['publicKey'], restarted.public_key)
        self.assertTrue(app.push_request('/api/push/status', {'endpoint': sub['endpoint']})['subscribed'])
        restarted.tick(); self.assertEqual(len(sent), 1)

    def test_human_approval_emits_one_durable_brain_notice(self):
        with patch('brain_server.StdioProxy', Proxy):
            app = Application(self.root, 'unused', self.root, self.root, '測試者')
        self.addCleanup(app.brain.close)
        app.backend.open()
        app.backend.proxy.approvals = lambda thread: [{'id': 'exact-approval', 'kind': 'permission'}]
        with app.brain.lock, app.brain.db: app.brain._set('enabled', True)
        app.brain.tick(); app.brain.tick()
        questions = [m for m in app.brain.state()['messages'] if m['kind'] == 'question']
        self.assertEqual(len(questions), 1)
        self.assertTrue(next(s for s in app.state()['notifications'] if s['id'] == 'brain')['unread'])
        self.assertEqual(app.backend.proxy.decisions, [])

    def test_human_approval_reaches_push_once_and_does_not_accept_it(self):
        with patch('brain_server.StdioProxy', Proxy):
            app = Application(self.root, 'unused', self.root, self.root, 'synthetic')
        self.addCleanup(app.brain.close)
        sent = []
        app.push = PushService(self.root, lambda: app.brain.notification_state(app.backend.state()),
                               'https://test.example', sender=lambda *args: sent.append(args[1]) or 201,
                               settle_seconds=0)
        key = ec.generate_private_key(ec.SECP256R1())
        app.push.subscribe({'endpoint': 'https://web.push.apple.com/synthetic-test',
                            'keys': {'p256dh': b64(public_bytes(key)), 'auth': b64(b'a' * 16)}})
        app.backend.open()
        app.backend.proxy.approvals = lambda thread: [{'id': 'synthetic-approval', 'kind': 'permission'}]
        with app.brain.lock, app.brain.db:
            app.brain._set('enabled', True)
        app.brain.tick()
        app.push.tick()
        app.brain.tick()
        app.push.tick()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['session'], 'brain')
        self.assertEqual(app.backend.proxy.decisions, [])


if __name__ == '__main__': unittest.main()
