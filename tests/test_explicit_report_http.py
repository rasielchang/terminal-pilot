"""Authenticated local HTTP, shipped worker CLI and existing Claude hook contract."""
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

import test_explicit_work_report as fixture
from pilot_http import WebServer


class ExplicitReportHTTPTests(unittest.TestCase):
    def test_cli_exact_worker_identity_receipt_status_and_auth(self):
        f=fixture.ExplicitReportTests();f.setUp();self.addCleanup(f.doCleanups)
        session=str(uuid.uuid4());s=f.f.h.sessions[0];s['id']=session
        f.data['session']=session
        app=SimpleNamespace(brain=f.b,token='synthetic',hosts={'127.0.0.1'},https_origin='',stylesheet_url='/test.css')
        server=WebServer(('127.0.0.1',0),app)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def stop():server.shutdown();server.server_close();thread.join()
        self.addCleanup(stop)
        origin='http://127.0.0.1:'+str(server.server_port);store=Path(f.f.temp.name)
        (store/'https-target.json').write_text(json.dumps({'origin':origin,'target':origin}))
        (store/'web-auth.json').write_text(json.dumps({'token':app.token}))
        (store/'sessions').mkdir();(store/'sessions'/(session+'.json')).write_text(json.dumps({'tool':'codex'}))
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','TERMINAL_PILOT_SESSION':session,'CODEX_THREAD_ID':'thread1'}
        tool=Path(__file__).resolve().parents[1]/'scripts/brain_tools.py'
        def cli(op,payload=None,*args):
            result=subprocess.run([sys.executable,'-B',str(tool),op,'--store',str(store),*args],
                env=env,input=json.dumps(payload),text=True,capture_output=True)
            return result.returncode,json.loads(result.stdout)
        with self.assertRaises(HTTPError) as error:
            build_opener(ProxyHandler({})).open(Request(origin+'/api/brain/report',data=json.dumps(f.data).encode(),headers={'Content-Type':'application/json'}))
        self.assertEqual(error.exception.code,401)
        code,context=cli('report-context');self.assertEqual(code,0);self.assertEqual(context['work'],f.data['work'])
        body={k:v for k,v in f.data.items() if k not in {'session','thread'}}
        code,first=cli('report',body);self.assertEqual(code,0);self.assertFalse(first['delivered'])
        f.b.tick();code,status=cli('report-status',None,'--report',first['id'])
        self.assertEqual(code,0);self.assertTrue(status['summarized']);self.assertIsNotNone(status['summary'])
        self.assertEqual(cli('report',body),(0,status))
        self.assertEqual(len(f.f.backend.sources),1)
        env['CODEX_THREAD_ID']='thread2';self.assertEqual(cli('report-context')[0],1)
        # Paired recovery explicitly names the original worker, without pretending to be Brain.
        env.pop('CODEX_THREAD_ID');self.assertEqual(cli('report-context',None,'--worker-thread','thread1')[0],0)
        # Claude workers already have a native, exact session binding in their hook journal.
        (store/'sessions'/(session+'.json')).write_text(json.dumps({'tool':'claude','claudeSessionID':'thread1'}))
        self.assertEqual(cli('report-context')[0],0)
        self.assertFalse(f.f.h.sent)

    @unittest.skipIf(os.name=='nt','Native macOS/Unix hook uses fcntl')
    def test_existing_claude_hook_explains_tool_without_sending_or_echoing_prompt(self):
        import bridge
        import tempfile
        with tempfile.TemporaryDirectory() as store:
            session=str(uuid.uuid4());thread=str(uuid.uuid4())
            payload={'hook_event_name':'UserPromptSubmit','session_id':thread,'prompt':'PRIVATE HUMAN TEXT'}
            value=bridge.on_hook(store,session,payload)
            context=value['hookSpecificOutput']
            self.assertEqual(context['hookEventName'],'UserPromptSubmit')
            self.assertIn('report-context',context['additionalContext'])
            self.assertIn('report-status',context['additionalContext'])
            self.assertNotIn('PRIVATE HUMAN TEXT',json.dumps(value))
            self.assertNotIn('permissionDecision',json.dumps(value))
            self.assertIsNone(bridge.on_hook(store,session,{**payload,'agent_id':'child'}))
            self.assertIsNone(bridge.on_hook(store,session,{**payload,'hook_event_name':'Stop'}))


if __name__=='__main__':unittest.main()
