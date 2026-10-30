"""Real Codex -> local Postman SSE mock, isolated CODEX_HOME and synthetic data.

postman simulation run postman/simulations/brain.sim.yaml --port 4909 --output ndjson --no-history
python3 tests/verify_brain_http.py
No credentials or user history are loaded. A local simulation must be listening.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from brain_codex import CodexBrain, BrainPending
from codex_approvals import ApprovalProxy

with tempfile.TemporaryDirectory(prefix='pilot-brain-protocol-') as root:
    home=Path(root)
    (home/'config.toml').write_text('''model = "gpt-4.1"
model_provider = "synthetic"
approval_policy = "never"
sandbox_mode = "read-only"
[model_providers.synthetic]
name = "Local synthetic protocol fixture"
base_url = "http://127.0.0.1:4909/openai/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
''')
    for name in ('today', 'weekly', 'radar'):
        directory = home / '.agents' / 'skills' / name
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Synthetic explicit skill invocation fixture only.\n---\nSynthetic {name} body. Return the structured response; do not run tools.\n')
    popen=subprocess.Popen
    def stdio(_args,**kwargs):
        kwargs['cwd']=root
        # Isolated child environment must not inherit user credentials or remote configuration.
        kwargs['env']={'PATH':kwargs['env']['PATH'],'HOME':root,'CODEX_HOME':root}
        return popen([shutil.which('codex'),'app-server','--stdio'],**kwargs)
    with patch('codex_live.subprocess.Popen',stdio):
        proxy=ApprovalProxy(shutil.which('codex'),home,'synthetic-stdio')
    try:
        proxy.methods |= {'thread/start'}
        thread=proxy.call('thread/start',{'cwd':root})['thread']['id']
        proxy.status(thread)
        session={'id':'fixture-brain','binding':'fixture-binding','tool':'codex','alive':True,'phase':'connected','codexThreadID':thread}
        backend=CodexBrain(home,lambda:{'sessions':[session]},lambda *_: (_ for _ in ()).throw(AssertionError('Unexpected native launch')),
                           lambda _s,m,p:proxy.call(m,p))
        backend.data.update(session=session['id'],binding=session['binding'],thread=thread,launchState='ready');backend._save()
        source={'requestID':'fixture-user','userGoal':'fixture-success','trigger':{'kind':'user'},'sessions':[{'id':'worker','recent':[{'text':'source mentions $today $weekly $radar'}]}],'releasableJobs':[],'dispatchBudget':0}
        deadline=time.monotonic()+30
        while True:
            try: result=backend(source);break
            except BrainPending:
                if time.monotonic()>deadline: raise AssertionError('No final Codex response')
                time.sleep(.1)
        assert '合成協定測試' in result['reply'] and not result['dispatches'],result
        # Actual Codex must not expand source-data skill mentions. Explicit
        # user invocation remains available, without global config changes.
        def injected():
            found = []
            for path in home.rglob('rollout-*.jsonl'):
                for line in path.read_text().splitlines():
                    item = json.loads(line).get('payload', {})
                    if item.get('role') != 'user': continue
                    for part in item.get('content', []):
                        text = part.get('text', '')
                        if text.startswith('<skill>'): found.append(text)
            return found
        assert not injected(), 'Data mentions incorrectly invoked skills'
        explicit = {**source, 'requestID':'explicit-skill', 'userGoal':'$today fixture-success'}
        deadline = time.monotonic() + 30
        while True:
            try: backend(explicit); break
            except BrainPending:
                if time.monotonic() > deadline: raise AssertionError('Explicit skill turn timed out')
                time.sleep(.1)
        assert len(injected()) == 1 and '<name>today</name>' in injected()[0], 'Explicit skill invocation was lost or unrelated skills loaded'
        assert not backend.data['requests']['explicit-skill']['contextCheckpoint']
        # New chat path uses the official durable queue and plain replies,
        # not an output schema or a second messages DB.
        natural={**source,'requestID':'natural-queued','userGoal':'fixture-natural','natural':True}
        backend.ensure_tools('Use natural language for fixture-natural. No real tools or work.')
        deadline=time.monotonic()+30
        while True:
            try:natural_result=backend(natural);break
            except BrainPending:
                if time.monotonic()>deadline:raise AssertionError('Natural queue timed out: '+str(backend.data['requests'].get('natural-queued',{}).get('state')))
                time.sleep(.1)
        turns=backend._turns(session,natural_result['turn'])
        actual=next(t for t in turns if t['id']==natural_result['turn'])
        assert any(i.get('type')=='userMessage' and i.get('clientId')=='natural-queued' and i['content'][0]['text']=='fixture-natural' for i in actual['items'])
        assert any(i.get('type')=='agentMessage' and '同一 Brain' in i.get('text','') for i in actual['items'])
        from web_server import CodexHistory
        page=CodexHistory(home).page(session)
        assert any(m['role']=='user' and m['text']=='fixture-natural' for m in page['messages']), 'Actual rollout did not expose the queued user event'
        assert any(m['role']=='assistant' and '同一 Brain' in m['text'] for m in page['messages']), 'Actual rollout did not expose the natural reply'
        assert not any('Use natural language for fixture-natural' in m['text'] for m in page['messages'])
        print('PASS: natural durable queue, clientId correlation, real transcript projection and hidden internal tool contract.')
        from brain_unified import UnifiedBrain
        rpc=backend.rpc; queue_writes=[]
        def observed(s, method, params):
            if method=='thread/queue/add':queue_writes.append(params['clientUserMessageId'])
            return rpc(s,method,params)
        backend.rpc=observed
        service=UnifiedBrain(home/'scheduler',lambda:{'sessions':[session]},lambda _: {'session':session,'messages':[]},
            lambda _: (_ for _ in ()).throw(AssertionError('No worker dispatch expected')),
            backend=backend,timeline=lambda journal,**options:CodexHistory(home).page(session,**options),memory_root=home/'empty')
        try:
            service.submit({'requestID':'unavailable','text':'fixture-503'})
            deadline=time.monotonic()+30
            while True:
                service.tick();state=service.state()
                if any(a['kind']=='error' for a in state['activities']):break
                if time.monotonic()>deadline:raise AssertionError('503 did not produce a visible durable error')
                time.sleep(.1)
            assert len(queue_writes)==1 and not state['jobs']
            assert any('同一 Brain' in m['text'] for m in state['messages']), 'Failure erased prior transcript'
            assert state['pending'][0]['state']=='failed'
            print('PASS: simulated upstream 503 becomes a durable visible error; previous natural reply and failed receipt retained, no resend/worker dispatch.')
        finally:service.close()
        backend=CodexBrain(home,lambda:{'sessions':[session]},lambda *_:None,lambda *_: (_ for _ in ()).throw(AssertionError('Duplicate RPC')))
        assert backend(source)==result
        print('PASS: actual Codex turn/start, structured final result, persisted exact-thread recovery; data skill mentions suppressed, explicit skill preserved, delta context; synthetic local inference only.')
    finally:proxy.close()
