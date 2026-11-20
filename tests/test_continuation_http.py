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
import test_local_closeout as closeout_fixture
from pilot_http import WebServer


class ContinuationHTTPTests(unittest.TestCase):
    def tool_server(self, f):
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
        return cli, env, origin, app.token

    def test_shipped_cli_direct_execution_and_status(self):
        f = fixture.ContinuationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        cli, env, origin, _ = self.tool_server(f)
        capabilities = cli('continuation-capabilities')
        self.assertFalse(capabilities['transcriptRoleProvesHuman'])
        self.assertFalse(capabilities['additionalConfirmationRequired'])
        payload = {k: v for k, v in f.data.items() if k not in {'session', 'thread'}}
        first = cli('continuation-propose', payload)
        self.assertEqual(first['receiptURL'], origin+'/continuation.html#'+first['id'])
        self.assertEqual(first['state'], 'authorized'); self.assertFalse(f.closed)
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


    def test_explicit_report_to_readback_through_shipped_cli_and_http(self):
        case = closeout_fixture.LocalCloseoutTests()
        case.setUp(); self.addCleanup(case.doCleanups); case.wire_asana()
        f = case.f
        cli, env, origin, token = self.tool_server(f)
        f.f.append('user', '回報給 Brain 並結案')
        worker = cli('report-context')
        report = cli('report', {'requestID': 'worker-report', 'work': worker['work'],
            'generation': worker['generation'], 'instruction': worker['instruction']['event'],
            'sources': f.data['sources'], 'confirmUserRequested': True})
        self.assertIsNone(report['continuation'])
        judgment = case.judgment(report)
        env['CODEX_THREAD_ID'] = 'brain-thread'
        context = cli('context')
        self.assertTrue(context['reportOnly'])
        self.assertEqual(context['explicitReport']['original']['text'], '回報給 Brain 並結案')
        plan = cli('continuation-plan', {k: v for k, v in judgment.items() if k != 'thread'})
        self.assertEqual(plan['state'], 'planned', plan)
        req = {'turn': judgment['turn'], 'report': report['id'], 'actions': plan['actions']}
        g = cli('continuation-propose', req, '--reported')
        self.assertEqual(g['state'], 'authorized')
        self.assertFalse(f.closed)
        self.assertTrue(all(m == 'GET' for m, _, _ in case.calls))
        case.delivered(report)
        retired = Request(origin+'/api/brain/continuation-confirm', data=json.dumps({'continuation':g['id']}).encode(),
            headers={'Content-Type':'application/json', 'Authorization':'Bearer '+token})
        with self.assertRaises(HTTPError) as error:
            build_opener(ProxyHandler({})).open(retired)
        self.assertEqual(error.exception.code, 404); error.exception.close()
        with build_opener(ProxyHandler({})).open(origin+'/continuation.html') as page:
            self.assertNotIn(b'id="confirm"', page.read())
        case.advance()
        active = f.active(g)
        execution = cli('continuation-run', {'turn': active['turn'], 'continuation': g['id']})
        self.assertEqual(execution['state'], 'completed', execution)
        self.assertEqual([m for m, _, _ in case.calls if m != 'GET'], ['POST', 'PUT'])
        self.assertTrue(execution['actions'][-1]['receipt']['closureProof']['absent'])
        self.assertEqual(len(f.closed), 1)
        status = cli('report-status', None, '--report', report['id'])
        self.assertTrue(status['delivered']); self.assertTrue(status['summarized'])
        self.assertEqual(status['continuation'], g['id'])
        self.assertEqual(cli('continuation-status', None, '--continuation', g['id'])['state'], 'completed')


if __name__ == '__main__': unittest.main()
