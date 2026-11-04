"""Current actionable notices are projections, never deletions or read receipts."""
import re
from brain_attention import requests_answer,acknowledgment


def action_summary(text):
    text=re.sub(r'```.*?```','',text,flags=re.S)
    clauses=[re.sub(r'[*#`>]+','',s).strip() for s in re.split(r'[\n。]',text)]
    # A conditional future operation in a long result is not a current request.
    choices=[s for s in clauses if requests_answer(s) and not re.search(r'如果|若|屆時|補跑|不催|無需|不用|不需要|不必|之後|未來',s)]
    return (choices[0] if choices else re.sub(r'\s+',' ',text).strip())[:90]


def wait_status(record, wait, fresh):
    if not record.get('active',True):return 'retired'
    if wait['kind']=='runtime':
        if record.get('runtimeCoverage')!='confirmed' or record.get('windowState')!='present':return 'unconfirmed'
        return 'current' if record.get('status')=='waiting' else 'superseded'
    if wait.get('answeredBy'):return 'answered'
    if wait.get('deferred') or re.search(r'不催|方便時|稍後再|暫不需要|無需現在',wait.get('text','')):return 'deferred'
    if record.get('runtimeCoverage')!='confirmed' or record.get('windowState')!='present':return 'unconfirmed'
    if record.get('coverage')!='current' or not fresh:return 'unconfirmed'
    evidence=[record.get(k) or {} for k in ('instruction','result','error')]
    if (record.get('latest') or {}).get('kind')=='error':evidence.append(record['latest'])
    evidence=[e for e in evidence if isinstance(e,dict)]
    evidence=[e for e in evidence if (e.get('source') or {}).get('revision')==record.get('sourceRevision')]
    evidence=[e for e in evidence if not (e.get('kind')=='instruction' and
        (acknowledgment(e.get('text','')) or re.search(r'[?？]$|目前進度|現在進度|目前狀況',e.get('text','').strip())))]
    latest=max(evidence,key=lambda e:(e.get('source') or {}).get('cursor',0),default={})
    sources=wait.get('source',[]);sources=[sources] if isinstance(sources,str) else sources
    if latest.get('event') not in sources:return 'superseded'
    if wait['kind']=='source':
        clauses=re.split(r'[\n。]',wait.get('text',''))
        if not any(requests_answer(s) and not re.search(r'如果|若|屆時|補跑|不催|無需|不用|不需要|不必|之後|未來',s) for s in clauses):return 'context_only'
    return 'current'


def notices(activities, observed, jobs, decisions, topics=()):
    actions=[];history=[];by_job={j['id']:j for j in jobs};decision_sessions={d['session']['id'] for d in decisions}
    completed={t['id'] for t in topics if t.get('state')=='done'}
    latest_activity={a.get('job'):a for a in activities if a.get('job') and a.get('notificationEligible')}
    seen=set()
    for work in observed:
        for wait in work['waiting']+work.get('waitingHistory',[]):
            identity=wait['id']
            if identity in seen:continue
            seen.add(identity)
            job=by_job.get(work.get('job'))
            item={'id':identity,'title':work['title'],'summary':wait['action'],'text':wait['text'],
                  'validity':wait['validity'],'session':work.get('session'),'generation':work.get('bindingGeneration'),
                  'job':job['id'] if job else None}
            if wait['validity']=='current' and work.get('session'):
                if wait['kind']=='runtime' and work['session'] in decision_sessions:continue
                actions.append(item)
            else:history.append(item)
    for a in activities:
        job=by_job.get(a.get('job'));current=bool(job and job['state'] in {'blocked','uncertain'} and a.get('notificationEligible') and not job.get('manualObservation'))
        if current:
            latest=latest_activity.get(job['id'],a)
            if latest['id']!=a['id']:
                if latest['text']==a['text']:continue
                current=False
            # Only an explicit later attempt for this task AND exact target can
            # supersede its old failure; sharing a project is not sufficient.
            target=(job.get('target') or {}).get('id')
            superseded=bool(job.get('topic') and target and any(j.get('topic')==job['topic'] and
                (j.get('target') or {}).get('id')==target and j.get('created',0)>job.get('created',0) for j in jobs))
            current=current and job.get('topic') not in completed and not superseded
        # Unresolved close/transport/coverage failures remain visible, once per
        # durable incident. Legacy unlinked text is history, not a new action.
        if not job:current=bool(a.get('notificationEligible') and not a.get('resolved'))
        item={'id':a['id'],'title':job['title'] if job else '執行紀錄','summary':action_summary(a['text']),
              'text':a['text'],'validity':'current' if current else 'history','job':job['id'] if job else None}
        # Repeated reports of one job/state are one action; distinct jobs remain separate.
        key=('job',a.get('job'),job['state']) if current and job else ('incident',a['id']) if current else ('history',a.get('job'),a['kind'],a['text'])
        if key in seen:continue
        seen.add(key)
        (actions if current else history).append(item)
    return {'actions':actions,'history':history}
