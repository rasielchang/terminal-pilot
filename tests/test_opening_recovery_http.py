"""Real local HTTP + shipped CLI, synthetic state, no real windows or models."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
from urllib.parse import urlencode
from types import SimpleNamespace

from test_opening_recovery import OpeningRecoveryTests
from pilot_http import WebServer


class RecoveryHTTPTests(unittest.TestCase):
    def test_formal_cli_auth_receipt_readback_retry_and_brain_authority(self):
        f = OpeningRecoveryTests(); f.setUp(); self.addCleanup(f.tearDown); f.withdraw()
        app = SimpleNamespace(brain=f.f.brain, token='synthetic-only', hosts={'127.0.0.1'}, https_origin='', stylesheet_url='/synthetic.css')
        server = WebServer(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def stop(): server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(stop)
        origin = 'http://127.0.0.1:' + str(server.server_port)
        store = Path(f.f.temp.name)
        (store/'https-target.json').write_text(json.dumps({'target':origin, 'origin':origin}))
        (store/'web-auth.json').write_text(json.dumps({'token':app.token}))
        tool = Path(__file__).resolve().parents[1]/'scripts/brain_tools.py'
        env = dict(os.environ); env.pop('CODEX_THREAD_ID', None)
        def cli(operation, payload=None, *args):
            result = subprocess.run([sys.executable, '-B', str(tool), operation, '--store', str(store), *args],
                                    input=json.dumps(payload), capture_output=True, text=True, env=env, check=False)
            return result.returncode, json.loads(result.stdout)
        with self.assertRaises(HTTPError) as unauthorized:
            build_opener(ProxyHandler({})).open(Request(origin+'/api/brain/recovery',
                data=json.dumps(f.request).encode(), headers={'Content-Type':'application/json'}))
        self.assertEqual(unauthorized.exception.code, 401)
        first_code, first = cli('recover-opening', f.request)
        self.assertEqual(first_code, 0); self.assertEqual(first['status'], 'recovered')
        self.assertEqual(cli('recover-opening', f.request), (first_code, first))
        self.assertEqual(len(f.proofs), 1)
        code, conflict = cli('recover-opening', {**f.request, 'reason':'changed'})
        self.assertEqual(code, 1); self.assertEqual(conflict['status'], 409)
        query = urlencode({k:f.request[k] for k in ('topic','target','opening','job')})
        request = Request(origin+'/api/brain/recovery?'+query, headers={'Authorization':'Bearer '+app.token})
        with build_opener(ProxyHandler({})).open(request) as response: saved = json.load(response)
        self.assertEqual(saved['receipt'], first); self.assertTrue(saved['readOnly'])
        env['CODEX_THREAD_ID'] = 'different-worker-thread'
        code, context = cli('context')
        self.assertEqual(code, 1); self.assertEqual(context['status'], 409)
        self.assertFalse(f.f.opened); self.assertFalse(f.f.h.sent)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(RecoveryHTTPTests)
