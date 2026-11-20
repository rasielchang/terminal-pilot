"""Synthetic browser fixture, real UI/HTTP/CLI. Never opens a real work window."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import test_local_closeout as fixture
from web_server import WebApplication
from pilot_http import WebServer

root=Path(__file__).resolve().parents[1]
output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True)
closeout=fixture.LocalCloseoutTests();closeout.setUp();closeout.wire_asana()
f=closeout.f
store=Path(f.f.temp.name)
session=str(uuid.uuid4());f.f.h.sessions[0].update(id=session,title='Synthetic closeout work')
f.data['session']=session
f.f.h.sessions[1]['title']='Synthetic unrelated work'
class Native:
    def call(self,method,data=None):
        if method=='list':return f.f.h.state()
        if method=='send':
            assert data['session']==session
            f.f.append('user',data['text'])
            pending.set()
            return {'sent':True}
        if method=='ack':return {'ok':True}
        raise ValueError('Synthetic fixture does not implement '+method)
pending=threading.Event()
app=WebApplication(Native(),root/'web',store)
app.brain=f.b;app.token='synthetic-only';app.state=f.f.h.state

def page(identity,**options):
    s=next(s for s in f.f.h.sessions if s['id']==identity)
    rows=f.f.pages['thread1' if identity==session else 'thread2']
    return {'session':s,'messages':copy.deepcopy(rows),'source':'codex','hasMore':False,
            'cursor':len(rows),'latestCursor':len(rows)}
app.thread=page;app.thread_page=page
server=WebServer(('127.0.0.1',0),app)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin='http://127.0.0.1:'+str(server.server_port)
(store/'https-target.json').write_text(json.dumps({'origin':origin,'target':origin}))
(store/'web-auth.json').write_text(json.dumps({'token':app.token}))
(store/'sessions').mkdir();(store/'sessions'/(session+'.json')).write_text('{"tool":"codex"}')
env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','TERMINAL_PILOT_SESSION':session,'CODEX_THREAD_ID':'thread1'}
env.pop('ASANA_TOKEN',None)
def cli(op,data=None,*args):
    p=subprocess.run([sys.executable,'-B',str(root/'scripts/brain_tools.py'),op,'--store',str(store),*args],
       env={**env,'CODEX_THREAD_ID':'thread1' if op in {'report','report-context'} else env['CODEX_THREAD_ID']},input=json.dumps(data),text=True,capture_output=True)
    if p.returncode:raise RuntimeError(p.stdout+p.stderr)
    return json.loads(p.stdout)
backend=f.f.backend;calls=[]
class Brain:
    def __getattr__(self,key):return getattr(backend,key)
    def __call__(self,source):
        calls.append(source['trigger']['kind'])
        if source['trigger']['kind']=='explicit_report':
            assert source['reportOnly']
            report_id = source['explicitReport']['report']
            report = f.b.work_report_status(report_id)
            req = closeout.judgment(report)
            env['CODEX_THREAD_ID'] = 'brain-thread'
            context = cli('context')
            assert context['explicitReport']['original']['id'] == req['instruction']
            plan = cli('continuation-plan', {k:v for k,v in req.items() if k != 'thread'})
            assert plan['state'] == 'planned', plan
            grant = cli('continuation-propose', {'turn':req['turn'],'report':report_id,'actions':plan['actions']}, '--reported')
            assert grant['state'] == 'authorized', grant
            backend.append('assistant', 'Synthetic report delivered. Closeout authorized.', req['turn'], 'final_answer')
            return {'turn': req['turn']}
        g=source['continuation'];request=f.active(g);env['CODEX_THREAD_ID']='brain-thread'
        c=cli('context');assert not c['reportOnly']
        receipt=cli('continuation-run',{'turn':request['turn'],'continuation':g['id']})
        assert receipt['state']=='completed',receipt
        backend.append('assistant','Synthetic delivery reviewed and exact window closed.',request['turn'],'final_answer')
        return {'turn':request['turn']}
f.b.backend=Brain()
print(json.dumps({'origin':origin,'session':session}),flush=True)
try:
    report=None
    while True:
        if pending.is_set() and report is None:
            ctx=cli('report-context')
            payload={'requestID':'ui-report','work':ctx['work'],'generation':ctx['generation'],
                     'instruction':ctx['instruction']['event'],'sources':f.data['sources'],'confirmUserRequested':True}
            report=cli('report',payload)
        f.b.tick()
        if report:
            status=cli('report-status',None,'--report',report['id'])
            receipt=cli('continuation-status',None,'--continuation',status['continuation']) if status['continuation'] else {}
            if receipt.get('summarized'):
                assert calls==['explicit_report','authorized_continuation'],calls
                assert len(f.closed)==1
                retried=cli('report',payload);assert retried['id']==report['id']
                (output/'result.json').write_text(json.dumps({'report':cli('report-status',None,'--report',report['id']),
                    'continuation':receipt,'brainTurns':calls,'closedCount':len(f.closed),
                    'unrelatedWorkPreserved':len(f.f.h.sessions)==1,
                    'adapters':['synthetic worker','synthetic Brain','synthetic native close', 'synthetic Asana']},ensure_ascii=False,indent=2))
        time.sleep(.1)
finally:
    server.shutdown();server.server_close();closeout.doCleanups()
