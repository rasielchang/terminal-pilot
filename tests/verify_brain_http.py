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
        backend=CodexBrain(home,lambda:{'sessions':[session]},lambda *_:None,lambda *_: (_ for _ in ()).throw(AssertionError('Duplicate RPC')))
        assert backend(source)==result
        print('PASS: actual Codex turn/start, structured final result, persisted exact-thread recovery; data skill mentions suppressed, explicit skill preserved, delta context; synthetic local inference only.')
    finally:proxy.close()
