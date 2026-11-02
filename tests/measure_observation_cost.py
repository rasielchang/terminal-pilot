"""Synthetic cost probe. Bytes/turns only; never fabricates provider token usage."""
import json
import test_brain_observer as fixture
from brain_context import prepare


def measure():
    f=fixture.ObserverTests('test_direct_work_without_jobs_and_after_delivery_stays_observed');f.setUp()
    try:
        first=dict(f.targets[0])
        f.targets=[{**first,'id':'target-'+str(i),'thread':'fixture-'+str(i),'title':'Work '+str(i)} for i in range(10)]
        f.pages={t['thread']:[] for t in f.targets};f.h.sessions=[]
        f.brain.tick()
        for t in f.targets:
            for _ in range(60):f.append('assistant','Ordinary progress. '*60,thread=t['thread'],phase='commentary')
        for _ in range(3):f.brain.tick()
        progress_calls=len(f.backend.sources)
        for t in f.targets:f.append('assistant','Tests passed; installation still pending.',thread=t['thread'])
        for _ in range(4):f.brain.tick()
        source=f.backend.sources[0]
        checkpoint,cursor,_=prepare(source,None,'','brain-thread','same-boundary')
        delta,_,_=prepare(source,cursor,'','brain-thread','same-boundary')
        return {'fixtureWorks':10,'progressEvents':600,'progressModelTurns':progress_calls,
                'resultEvents':10,'resultModelTurns':len(f.backend.sources),
                'contextCheckpointBytes':len(checkpoint.encode()),'unchangedContextDeltaBytes':len(delta.encode()),
                'fixtureHistoryBytes':len(json.dumps(f.pages).encode()),
                'measuredProviderTokens':None,'note':'Same context checkpoint versus delta, not a claim about historical production billing.',
                'metrics':f.brain.work_state()['metrics']}
    finally:f.tearDown()


if __name__=='__main__':print(json.dumps(measure(),indent=2))
