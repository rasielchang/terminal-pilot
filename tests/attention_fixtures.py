"""Isolated durable receipts; no model, network, or real work."""

def outcome(f, text='Tested deliverable', *, session='one', kind='completed', identity='job'):
    target=next(t for t in f.targets if t['thread']==('thread1' if session=='one' else 'thread2'))
    with f.brain.db:
        goal=f.brain._message('Original authorized fixture goal',role='user')
        job={'id':identity,'state':kind,'session':session,'title':'Fixture','target':target,
             'goal':goal,'topic':'','instruction':'Test fixture','result':text,'created':f.h.now,
             'brainThread':'brain-thread','binding':next(s['binding'] for s in f.h.sessions if s['id']==session)}
        f.brain._save('jobs',job); f.brain._event(job,kind,text)
    return job

def legacy(f, *, identity='legacy', accepted=False):
    f.append('assistant','Old observation'); f.brain.observer.reconcile()
    event=f.events('result')[-1]
    with f.brain.db:
        goal=f.brain._message('Old goal',role='user')
        f.brain._save('work_deliveries',{'id':identity,'state':'queued','events':[event],
            'thread':'brain-thread','through':event['revision'] if 'revision' in event else f.record()['revision'],'attempts':0})
        f.brain._save('turns',{'id':identity,'state':'queued','goal':goal,'topic':'',
            'trigger':{'kind':'observation','reportOnly':True,'delivery':identity}})
        if accepted:f.backend.data['requests'][identity]={'state':'adding'}
    return identity
