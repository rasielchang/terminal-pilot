"""Durable report-only delivery and source-linked work summaries.

No worker IO belongs here. The only model destination is the original Brain.
Cursor advancement, outbox creation and turn creation share one transaction.
"""
import json
from brain_observer import digest


class WorkReports:
    def __init__(self, owner):
        self.owner = owner
        with owner.lock, owner.db:
            owner.db.execute('CREATE TABLE IF NOT EXISTS work_deliveries(id TEXT PRIMARY KEY,data TEXT NOT NULL)')

    def stage(self):
        o = self.owner
        with o.lock, o.db:
            # Backpressure: keep later events durable, not thousands of model turns.
            if any(d['state'] in {'queued','running','retry'} for d in o._rows('work_deliveries')): return
            cursor = o._get('workDeliveryCursor', 0)
            rows = o.db.execute('SELECT revision,data FROM work_events WHERE revision>? ORDER BY revision LIMIT 500', (cursor,)).fetchall()
            events, size, through = [], 0, cursor
            for revision, raw in rows:
                event = json.loads(raw)
                if event['notify']:
                    if events and (len(events) >= 8 or size + len(raw.encode()) > 16000): break
                    events.append({**event, 'revision':revision}); size += len(raw.encode())
                through = revision
            if through == cursor: return
            o._set('workDeliveryCursor', through)
            if not events: return
            identity = 'observation:' + str(cursor) + ':' + str(through)
            delivery = {'id':identity, 'state':'queued', 'events':events, 'created':o.clock(),
                        'thread':o.backend.data.get('thread','') if o.backend else '', 'through':through, 'attempts':0}
            o._save('work_deliveries', delivery)
            o._save('turns', {'id':identity, 'goal':0, 'topic':'', 'state':'queued',
                'trigger':{'kind':'observation','reportOnly':True,'delivery':identity,'title':'已綁定工作有新狀態'}})

    def source(self, turn):
        o = self.owner
        delivery = next(d for d in o._rows('work_deliveries') if d['id'] == turn['trigger']['delivery'])
        ids = {e['work'] for e in delivery['events']}
        works = [o.observer.project(r) for r in o.observer.records() if r['id'] in ids]
        return {'requestID':turn['id'], 'brainThread':delivery['thread'], 'scope':{'id':'','title':'工作觀察','goal':''},
                'userGoal':'', 'trigger':{**turn['trigger'], 'events':delivery['events']},
                'reportOnly':True, 'works':works, 'topics':[], 'jobs':[], 'sessions':[], 'targets':[],
                'dispatchBudget':0, 'releasableJobs':[], 'busySessions':[], 'busyTargets':[],
                'natural':True, 'conversation':[], 'memory':[]}

    def allowed(self, turn, state):
        """Never reopen a Brain window or reroute a delivery to a new thread."""
        o = self.owner
        if not o.backend or not o.backend.snapshot(state.get('sessions',[])).get('connected'): return False
        if turn['trigger'].get('delivery'):
            d = next(d for d in o._rows('work_deliveries') if d['id'] == turn['trigger']['delivery'])
            thread = o.backend.data.get('thread')
            if d.get('thread') and d['thread'] != thread: return False
            if not d.get('thread'):
                d['thread'] = thread; o._save('work_deliveries',d)
        return True

    def update(self, turn, state, error=''):
        o = self.owner
        identity = turn['trigger'].get('delivery')
        if not identity: return
        d = next(d for d in o._rows('work_deliveries') if d['id']==identity)
        d.update(state=state, error=error, updated=o.clock())
        if state == 'retry': d['attempts'] += 1
        if state == 'done':
            d['brainTurn'] = turn['brainTurn']
            for activity in o._rows('activities'):
                if activity['id']=='failure:'+turn['id']:
                    activity['resolved']=True; o._save('activities',activity)
        o._save('work_deliveries',d)

    def metrics(self):
        o = self.owner
        with o.lock:
            kinds = {kind:count for kind,count in o.db.execute("SELECT json_extract(data,'$.kind'),COUNT(*) FROM work_events GROUP BY json_extract(data,'$.kind')")}
            important=o.db.execute("SELECT COUNT(*) FROM work_events WHERE json_extract(data,'$.notify')=1").fetchone()[0]
            deliveries=o._rows('work_deliveries')
            requests=o.backend.data.get('requests',{}) if o.backend else {}
            return {'events':sum(kinds.values()), 'kinds':kinds, 'important':important,
                    'progressWithoutModelTurn':kinds.get('progress',0),
                    'deliveries':len(deliveries), 'completed':sum(d['state']=='done' for d in deliveries),
                    'failed':sum(d['state']=='failed' for d in deliveries),
                    'retries':sum(d['attempts'] for d in deliveries),
                    'contextBytes':sum(requests.get(d['id'],{}).get('contextBytes',0) for d in deliveries),
                    'scannedBytes':o._get('observationScannedBytes',0),
                    'tokens':None, 'tokenUsageAvailable':False}

    def deliveries(self):
        return [{k:d.get(k) for k in ('id','state','thread','through','attempts','error','brainTurn')}
                for d in self.owner._rows('work_deliveries')[-30:]]


class ReportingAPI:
    def work_state(self, *, after=0, limit=100):
        if not self.observer: raise ValueError('此 host 尚未提供工作觀察。')
        with self.lock:
            return {**self.observer.snapshot(after=after,limit=limit),
                    'deliveries':self.reports.deliveries(), 'metrics':self.reports.metrics()}

    def reconcile_works(self, data):
        if set(data)-{'limit','after','pageSize'}: raise ValueError('補查只接受已綁定工作，不接受路徑或新對話。')
        limit=data.get('limit',32)
        if type(limit) is not int or not 1<=limit<=100: raise ValueError('補查數量須為 1 至 100。')
        after,size=data.get('after',0),data.get('pageSize',100)
        if type(after) is not int or after<0 or type(size) is not int or not 1<=size<=200: raise ValueError('工作查詢游標或頁面大小無效。')
        self.observer.reconcile(limit=limit)
        # A query never calls the model. Delivery is handled by the normal loop.
        return self.work_state(after=after,limit=size)

    def observation_source(self, work, event):
        with self.lock:
            row=self.db.execute('SELECT data FROM work_events WHERE id=? AND observer=?',(event,work)).fetchone()
            if not row: raise ValueError('找不到此已綁定工作的來源事件。')
            return {'reportOnly':True, 'event':json.loads(row[0])}

    def summarize_work(self, data):
        required={'thread','turn','requestID','work','revision','sources','summary','waiting'}
        if set(data)!=required: raise ValueError('摘要欄位不符合工作觀察契約。')
        with self.lock,self.db:
            fingerprint, old=self._receipt(data)
            if old: return old
            if not self.backend or data['thread']!=self.backend.data.get('thread'): raise ValueError('摘要不屬於原 Brain。')
            active=self.backend.current_turn()
            if active.get('id')!=data['turn'] or active.get('status')!='inProgress': raise ValueError('摘要的 Brain 回合已改變。')
            r=next((r for r in self.observer.records() if r['id']==data['work']),None)
            if not r or not r['active'] or type(data['revision']) is not int or r['revision']!=data['revision']: raise ValueError('工作已有較新觀察，請重新補查摘要。')
            sources=data['sources']; summary=data['summary']; wait=data['waiting']
            if not isinstance(sources,list) or not 1<=len(sources)<=12 or any(not isinstance(s,str) for s in sources): raise ValueError('摘要需要 1 至 12 筆原事件。')
            if not isinstance(summary,dict) or set(summary)!={'decision','blocker','next','result'} or any(not isinstance(v,str) or len(v)>600 for v in summary.values()): raise ValueError('摘要每欄最多 600 字。')
            if not isinstance(wait,str) or wait not in {'pending','resolved','unchanged'}: raise ValueError('待本人狀態無效。')
            if wait=='pending' and not summary['blocker'].strip(): raise ValueError('待本人狀態需要具體原因。')
            evidence=[self.observation_source(r['id'],s)['event'] for s in sources]
            if any(e['kind']=='summary' for e in evidence): raise ValueError('摘要不可只引用另一份摘要。')
            latest=self.db.execute("SELECT id FROM work_events WHERE observer=? AND json_extract(data,'$.kind') IN ('instruction','result','error') ORDER BY revision DESC LIMIT 1",(r['id'],)).fetchone()
            if latest and latest[0] not in sources: raise ValueError('摘要不可略過最新決定或結果。')
            if wait=='resolved' and (r['coverage']!='current' or not self.observer.fresh(r) or (r.get('waiting') or {}).get('kind')=='runtime'):
                raise ValueError('來源缺口或正式待核准不能由摘要解除。')
            if wait=='resolved' and r.get('semanticWaiting'):
                previous=r['semanticWaiting']['revision']
                newest=max(self.db.execute('SELECT revision FROM work_events WHERE id=?',(s,)).fetchone()[0] for s in sources)
                if newest<=previous: raise ValueError('解除待本人需要較新的來源證據。')
            identity='summary:'+digest(data)
            self.observer.event(r,identity,'summary',notify=False,source={'events':sources,'brainTurn':data['turn']})
            r['summary']={'fields':summary,'sources':sources,'brainTurn':data['turn'],'revision':r['revision']}
            if wait=='pending': r['semanticWaiting']={'id':identity,'kind':'semantic','text':summary['blocker'], 'source':sources,'revision':r['revision']}
            elif wait=='resolved': r['semanticWaiting']=None
            self.observer.save(r)
            return self._record(data,fingerprint,{'ok':True,'reportOnly':True,'work':r['id'],'revision':r['revision']})
