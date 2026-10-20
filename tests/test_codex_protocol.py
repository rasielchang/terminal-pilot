"""Opt-in, real Codex protocol + authenticated HTTP integration test.

PILOT_CODEX_PROTOCOL_TEST=1 python3 -m unittest discover -s tests -p test_codex_protocol.py
Uses a temporary CODEX_HOME and a synthetic MCP tool. No model, API key,
network request, real permission grant, or user conversation is involved.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from codex_approvals import ApprovalProxy
from codex_live import CodexLiveStatus
import test_web


@unittest.skipUnless(os.environ.get('PILOT_CODEX_PROTOCOL_TEST') == '1', 'Opt-in real Codex protocol test')
class CodexProtocolTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown
    request = test_web.WebTests.request
    # Inherit only the HTTP fixture, not the ordinary web test cases.
    def test_real_elicitation_round_trip_through_paired_http(self):
        binary = shutil.which('codex')
        if not binary: self.skipTest('Codex unavailable')
        fixture = Path(__file__).with_name('fixtures') / 'approval_mcp.py'
        (self.home/'config.toml').write_text('[mcp_servers.pilot_synthetic]\ncommand = '+json.dumps(sys.executable)+
            '\nargs = ['+json.dumps(str(fixture.resolve()))+']\n')
        original_popen = subprocess.Popen
        def stdio_transport(_command, **kwargs):
            return original_popen([binary,'app-server','--stdio'], **kwargs)
        with patch('codex_live.subprocess.Popen', stdio_transport):
            proxy = ApprovalProxy(binary, self.home, '/synthetic/stdio')
        try:
            proxy.methods = proxy.methods | {'thread/start','mcpServer/tool/call'}
            thread=str(uuid.uuid4())
            stamp=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
            rollout=self.home/'sessions'/('rollout-2026-10-17T00-00-00-'+thread+'.jsonl')
            rollout.parent.mkdir()
            rollout.write_text(json.dumps({'timestamp':stamp,'type':'session_meta','payload':{
                'id':thread,'timestamp':stamp,'cwd':str(self.home),'originator':'codex_cli_rs',
                'cli_version':'0.162.1','source':'cli','model_provider':'openai',
                'base_instructions':{'text':'Synthetic protocol test; no model turn.'}}})+'\n')
            # Load only a synthetic on-disk conversation in the temporary CODEX_HOME.
            try: proxy.call('thread/resume',{'threadId':thread,'excludeTurns':True})
            except Exception as error: raise AssertionError(getattr(error,'detail',str(error))) from error
            self.assertEqual(proxy.status(thread),'idle')
            session = {'id':'pilot-1','binding':'123:1:tty1','tool':'codex','phase':'running',
                       'codexThreadID':thread,'codexHome':str(self.home),'codexSocket':'/synthetic/stdio','agentPID':123}
            self.native.sessions = [session]
            live = CodexLiveStatus(binary,self.home,lambda:{'sessions':[session]},lambda *_:None)
            key=live.identity(session); live.clients[live.scope(key)]=proxy
            self.app.live=live
            for action in ('accept','decline'):
                with self.subTest(action=action), ThreadPoolExecutor(1) as pool:
                    future=pool.submit(proxy.call,'mcpServer/tool/call',{'threadId':thread,'server':'pilot_synthetic','tool':'confirm','arguments':{}})
                    deadline=time.monotonic()+3
                    while not proxy.approvals(thread) and time.monotonic()<deadline: time.sleep(.01)
                    live.cache[key]=(time.monotonic(),'approval')
                    code,_,body=self.request('/api/state')
                    self.assertEqual(code,200)
                    approvals=json.loads(body)['sessions'][0]['runtime']['approvals']
                    self.assertEqual(len(approvals),1)
                    self.assertIn('No files or applications',approvals[0]['detail'])
                    payload={'session':'pilot-1','binding':'123:1:tty1','requestID':'decision-'+action,
                             'approval':approvals[0]['id'],'action':action,'content':{}}
                    self.assertEqual(self.request('/api/approval',payload,auth=False)[0],401)
                    code,_,body=self.request('/api/approval',payload)
                    self.assertEqual(code,200); self.assertTrue(json.loads(body)['resolved'])
                    self.assertEqual(future.result(timeout=2)['content'][0]['text'],'synthetic decision: '+action)
                    self.assertEqual(proxy.approvals(thread),[])
                    self.assertEqual(self.request('/api/approval',payload)[0],200)
        finally: proxy.close()

