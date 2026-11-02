"""Replay identical synthetic sources against a chosen checkout, without network."""
import argparse
import json
from pathlib import Path
import sys
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1])
args=parser.parse_args()
sys.path[:0]=[str(args.source/'tests'),str(args.source/'scripts')]
import test_brain_observer as fixture
from brain_timeline import claude_visible
from web_push import PushService
from test_push import subscription

f=fixture.ObserverTests('test_direct_work_without_jobs_and_after_delivery_stays_observed');f.setUp()
try:
    f.brain.tick();sent=[];snapshots={}
    push=PushService(Path(f.temp.name)/'push',lambda:f.brain.notification_state(f.h.state()),
        'https://fixture.example',sender=lambda _s,p,_k,_u:sent.append(p) or 201,
        clock=lambda:f.h.now,settle_seconds=0)
    push.subscribe(subscription())
    def tick(delay=0):
        f.h.now+=delay;f.brain.tick();push.tick()
    def record(name):snapshots[name]={'modelCalls':len(f.backend.sources),'pushAttempts':len(sent)}
    for n in range(20):
        raw={'type':'assistant','uuid':'progress-'+str(n),'sessionId':'thread1','requestId':'turn-'+str(n),
            'timestamp':'1970-01-01T00:16:42Z','message':{'role':'assistant','stop_reason':'tool_use',
            'content':[{'type':'text','text':'正在查 B 組。'}]}}
        f.pages['thread1'].append(claude_visible(raw,n,'thread1'));tick(16)
    record('toolProgress20')
    f.append('user','執行了');tick(1);tick(16);record('humanAcknowledgment')
    f.append('user','Use the staged build');tick(1)
    f.append('assistant','Tests passed; staged build ready.');tick(4);tick(16);record('relatedDecisionAndResult')
    for _ in range(3):
        r=f.record()
        f.brain.summarize_work({'thread':'brain-thread','turn':f.backend.turn['id'],'requestID':'projection-'+str(_),
          'work':r['id'],'revision':r['revision'],'sources':[r['latest']['event']],
          'summary':{'decision':'Staged build','blocker':'','next':'Review','result':'Passed'},'waiting':'unchanged'})
        tick(16)
    record('summaryWriteback3')
    f.append('assistant','Please choose A or B');tick(1);tick(16);record('newHumanQuestion')
    f.append('assistant','Build failed; blocked.',thread='thread2',phase='error');tick(1);tick(16);record('otherWorkFailure')
    print(json.dumps({'scenario':snapshots,'modelCalls':len(f.backend.sources),'pushAttempts':len(sent),
        'workerCommands':len(f.h.sent),'deviceDeliveryVerified':False,'realModelUsed':False,
        'providerTokens':None,'contextBytes':sum(len(json.dumps(s,ensure_ascii=False).encode()) for s in f.backend.sources)},indent=2))
finally:f.tearDown()
