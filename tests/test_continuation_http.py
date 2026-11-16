"""Shipped CLI -> authenticated service -> native proof adapter -> exact Brain tool.
The synthetic native/Brain adapters deliberately cannot reach real work or Asana.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
import uuid
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import test_brain_continuations as fixture
from pilot_http import WebServer


class ContinuationHTTPTests(unittest.TestCase):
    def test_shipped_cli_scope_consent_execution_and_status(self):
        f = fixture.ContinuationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        session = str(uuid.uuid4()); f.f.h.sessions[0]['id'] = session; f.data['session'] = session
        app = SimpleNamespace(brain=f.b, token='synthetic-only', hosts={'127.0.0.1'}, https_origin='',
            stylesheet_url='/style.css', assets=Path(__file__).resolve().parents[1]/'web', stylesheet=b'')
        server = WebServer(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        origin = 'http://127.0.0.1:' + str(server.server_port); store = Path(f.f.temp.name)
        (store/'https-target.json').write_text(json.dumps({'target': origin, 'origin': origin}))
        (store/'web-auth.json').write_text(json.dumps({'token': app.token}))
        (store/'sessions').mkdir(); (store/'sessions'/(session+'.json')).write_text('{"tool":"codex"}')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TERMINAL_PILOT_SESSION=session, CODEX_THREAD_ID='thread1')
        tool = Path(__file__).resolve().parents[1]/'scripts/brain_tools.py'
        def cli(op, body=None, *args):
            p = subprocess.run([sys.executable, '-B', str(tool), op, '--store', str(store), *args],
                env=env, input=json.dumps(body), text=True, capture_output=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            return json.loads(p.stdout)
        capabilities = cli('continuation-capabilities')
        self.assertFalse(capabilities['transcriptRoleProvesHuman'])
        self.assertFalse(capabilities['nativeOwnerVerification'])
        self.assertTrue(capabilities['nativeScopeConfirmation'])
        self.assertFalse(capabilities['requiresSystemAuthentication'])
        self.assertEqual(capabilities['authorizationMethod'], 'native_scope_confirmation')
        payload = {k: v for k, v in f.data.items() if k not in {'session', 'thread'}}
        first = cli('continuation-propose', payload)
        self.assertEqual(first['confirmationURL'], origin+'/continuation.html#'+first['id'])
        self.assertEqual(first['state'], 'needs_confirmation'); self.assertFalse(f.closed)
        with self.assertRaises(HTTPError) as error:
            build_opener(ProxyHandler({})).open(Request(origin+'/api/brain/continuation-confirm',
                data=json.dumps({'continuation':first['id']}).encode(), headers={'Content-Type':'application/json'}))
        self.assertEqual(error.exception.code, 401); error.exception.close()
        # The shared bearer can request a dialog, never provide the approval proof.
        with self.assertRaises(HTTPError) as error:
            build_opener(ProxyHandler({})).open(Request(origin+'/api/brain/continuation-confirm',
                data=json.dumps({'continuation':first['id'], 'approved':True,
                    'method':'native_scope_confirmation'}).encode(),
                headers={'Content-Type':'application/json', 'Authorization':'Bearer '+app.token}))
        self.assertEqual(error.exception.code, 409); error.exception.close()
        self.assertNotIn('authorization', f.b.continuations.status(first['id']))
        cli('continuation-confirm', None, '--continuation', first['id'])
        with f.b.continuations.prompt_lock: pass
        f.b.continuations.stage(); active = f.active(first); env['CODEX_THREAD_ID'] = 'brain-thread'
        context = cli('context'); self.assertEqual(context['continuation']['state'], 'queued')
        receipt = cli('continuation-run', {'turn': active['turn'], 'continuation': first['id']})
        self.assertEqual(receipt['state'], 'completed', receipt['error']); self.assertEqual(len(f.closed), 1)
        f.f.backend.append('assistant', 'Synthetic continuation completed once.', active['turn'], 'final_answer')
        with f.b.lock, f.b.db:
            turn = next(t for t in f.b._rows('turns') if t['id'] == first['id'])
            turn.update(state='done', brainTurn=active['turn']); f.b._save('turns', turn); f.b.continuations.update(turn, 'done')
        status = cli('continuation-status', None, '--continuation', first['id'])
        self.assertTrue(status['summarized']); self.assertTrue(status['delivered'])
        self.assertEqual(cli('continuation-run', {'turn': active['turn'], 'continuation': first['id']})['state'], 'completed')
        self.assertEqual(len(f.closed), 1)
        for path in ('/continuation.html', '/continuation.js'):
            with build_opener(ProxyHandler({})).open(origin+path) as response: self.assertEqual(response.status, 200)


if __name__ == '__main__': unittest.main()
