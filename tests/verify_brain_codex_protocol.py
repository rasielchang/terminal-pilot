"""Read a synthetic completed turn through the real installed Codex protocol.
No inference, user config, credentials, Terminal UI or user history is accessed.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from codex_approvals import ApprovalProxy
from brain_codex import CodexBrain, BrainPending

with tempfile.TemporaryDirectory(prefix='pilot-brain-read-') as root:
    home=Path(root); thread=str(uuid.uuid4()); turn=str(uuid.uuid4())
    stamp=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    (home/'config.toml').write_text('''model = "gpt-4.1"
model_provider = "synthetic"
[model_providers.synthetic]
name = "Unreachable local test fixture"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
''')
    plan={'reply':'合成 Codex 歷史已讀取。','dispatches':[],'release':[]}
    records=[{'type':'session_meta','payload':{'id':thread,'timestamp':stamp,'cwd':root,'originator':'codex_cli_rs','cli_version':'0.163.0','source':'cli','model_provider':'synthetic','base_instructions':{'text':'Synthetic, no model calls.'}}},
             {'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'model_context_window':200000}},
             {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'[Pilot Brain 回合 fixture-user]\n合成測試'}]}},
             {'type':'event_msg','payload':{'type':'user_message','message':'[Pilot Brain 回合 fixture-user]\n合成測試','images':[]}},
             {'type':'response_item','payload':{'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':json.dumps(plan)}]}},
             {'type':'event_msg','payload':{'type':'agent_message','message':json.dumps(plan),'phase':'final_answer'}},
             {'type':'event_msg','payload':{'type':'task_complete','turn_id':turn,'last_agent_message':json.dumps(plan)}}]
    (home/'sessions').mkdir()
    (home/'sessions'/('rollout-'+thread+'.jsonl')).write_text(''.join(json.dumps({'timestamp':stamp,**r})+'\n' for r in records))
    popen=subprocess.Popen
    def stdio(_args,**kwargs):
        kwargs['cwd']=root
        kwargs['env']={'PATH':kwargs['env']['PATH'],'HOME':root,'CODEX_HOME':root}
        return popen([shutil.which('codex'),'app-server','--stdio'],**kwargs)
    with patch('codex_live.subprocess.Popen',stdio):proxy=ApprovalProxy(shutil.which('codex'),home,'synthetic-stdio')
    try:
        proxy.methods |= {'thread/start'}
        empty=proxy.call('thread/start',{'cwd':root})['thread']['id']
        proxy.call('thread/resume',{'threadId':thread,'excludeTurns':True})
        assert proxy.status(thread)=='idle'
        session={'id':'fixture-brain','binding':'fixture-binding','tool':'codex','alive':True,'phase':'complete','codexThreadID':thread}
        backend=CodexBrain(home,lambda:{'sessions':[session]},lambda *_:None,lambda _s,m,p:proxy.call(m,p))
        backend.data.update(session=session['id'],binding=session['binding'],thread=thread,launchState='ready',requests={'fixture-user':{'thread':thread,'turn':turn,'state':'running'}})
        try: result=backend({'requestID':'fixture-user'})
        except Exception as error: raise AssertionError(getattr(error,'detail',str(error))) from error
        assert result==plan,result
        source={'requestID':'new-synthetic-turn','userGoal':'Synthetic transport check only','trigger':{'kind':'user'},'sessions':[],'releasableJobs':[],'dispatchBudget':0}
        try: backend(source)
        except BrainPending: pass
        assert backend.data['requests']['new-synthetic-turn'].get('turn'), 'Real Codex rejected turn/start'
        empty_session={**session,'codexThreadID':empty}
        empty_backend=CodexBrain(home/'empty-runtime',lambda:{'sessions':[empty_session]},lambda *_:None,lambda _s,m,p:proxy.call(m,p))
        empty_backend.data.update(session=session['id'],binding=session['binding'],thread=empty,launchState='ready')
        try: empty_backend(source)
        except BrainPending: pass
        assert empty_backend.data['requests']['new-synthetic-turn'].get('turn'), 'New TUI first message was never started'
        print('PASS: real Codex 0.163.0 exact resume, turn pagination/final extraction, turn/start with client ID/output schema accepted; provider isolated to unreachable loopback, no real inference.')
    finally:proxy.close()
