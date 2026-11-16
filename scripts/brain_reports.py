"""Durable observation receipts and source-linked work summaries.

Observation alone never schedules inference. Explicit source-backed reports and
delegated job outcomes have separate durable identities and report-only turns.
"""
import json
from brain_observer import digest
from brain_attention import reportable, answers_wait


class WorkReports:
    def __init__(self, owner):
        self.owner = owner
        with owner.lock, owner.db:
            owner.db.execute('CREATE TABLE IF NOT EXISTS work_deliveries(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            owner.db.execute('CREATE TABLE IF NOT EXISTS work_delivery_events(id TEXT PRIMARY KEY,data TEXT NOT NULL,delivery TEXT)')
            # Backfill exact receipts, including queued/running deliveries. This
            # protects already-sent sources when upgrading or replaying cursors.
            for delivery in owner._rows('work_deliveries'):
                for event in delivery['events']:
                    owner.db.execute('INSERT OR IGNORE INTO work_delivery_events VALUES(?,?,?)',
                        (event['id'],json.dumps(event,ensure_ascii=False),delivery['id']))

    def stage(self):
        o = self.owner
        with o.lock, o.db:
            cursor=o._get('workDeliveryCursor',0)
            rows=o.db.execute('SELECT revision,data FROM work_events WHERE revision>? ORDER BY revision LIMIT 500',(cursor,)).fetchall()
            for revision,raw in rows:
                event=json.loads(raw)
                if reportable(event):
                    event['revision']=revision
                    o.db.execute('INSERT OR IGNORE INTO work_delivery_events VALUES(?,?,NULL)',
                                 (event['id'],json.dumps(event,ensure_ascii=False)))
                cursor=revision
            o._set('workDeliveryCursor',cursor)
            # Quiet only unsent legacy batches. An accepted/uncertain remote
            # queue request must finish/reconcile under its original identity.
            requests=o.backend.data.get('requests',{}) if o.backend else {}
            turns={t['id']:t for t in o._rows('turns')}
            for d in o._rows('work_deliveries'):
                t=turns.get(d['id'],{})
                if not d.get('explicit') and d['state']=='queued' and t.get('state')=='queued' and d['id'] not in requests:
                    d.update(state='silent',reason='sync_only');t.update(state='done',quiet=True)
                    o._save('work_deliveries',d);o._save('turns',t)
            # The source journal is consumed durably for synchronization only.
            # Delegated outcomes use one job-owned request; waiting cards do not
            # need a model round trip. This also retires unsent legacy events.
            o.db.execute("UPDATE work_delivery_events SET delivery='silent' WHERE delivery IS NULL")

    def notices(self):
        o=self.owner
        with o.lock:
            requests=o.backend.data.get('requests',{}) if o.backend else {}
            result={}
            for delivery in o._rows('work_deliveries'):
                turn=delivery.get('brainTurn') or requests.get(delivery['id'],{}).get('turn')
                if not turn:continue
                # Legacy observation turns remain in history, never a new
                # attention source. Already accepted remote requests reconcile
                # under their original ID; they cannot create a second alert.
                sources=[delivery['id']] if delivery.get('explicit') else []
                result[turn]={'notify':bool(sources),'noticeID':'observation:'+digest(sources)[:40],'attentionIDs':sources}
            return result

    def source(self, turn):
        o = self.owner
        delivery = next(d for d in o._rows('work_deliveries') if d['id'] == turn['trigger']['delivery'])
        ids = {e['work'] for e in delivery['events']}
        works = []
        for record in o.observer.records():
            if record['id'] not in ids:continue
            work=o.observer.project(record)
            fresh={e['id'] for e in delivery['events'] if e['work']==record['id']}
            if work.get('instruction',{}).get('event') not in fresh and work.get('instruction'):
                work['contextInstruction']={**work['instruction'],'text':work['instruction'].get('text','')[:600],'syncOnly':True}
            # Never re-present an unrelated old result/summary as this batch's
            # new finding. Current unresolved waits remain useful context.
            for key in ('instruction','result','latest'):
                if work.get(key,{}).get('event') not in fresh:work.pop(key,None)
            if not fresh.intersection(work.get('summary',{}).get('sources',[])):work.pop('summary',None)
            works.append(work)
        return {'requestID':turn['id'], 'brainThread':delivery['thread'], 'scope':{'id':'','title':'工作觀察','goal':''},
                'userGoal':'', 'trigger':{**turn['trigger'], 'events':delivery['events']},
                'reportOnly':True, 'works':works, 'topics':[], 'jobs':[], 'sessions':[], 'targets':[],
                'dispatchBudget':0, 'releasableJobs':[], 'busySessions':[], 'busyTargets':[],
                'natural':True, 'conversation':[], 'memory':[]}

    def allowed(self, turn, state):
        """Never reopen a Brain window or reroute a delivery to a new thread."""
        o = self.owner
        if not o.backend or not o.backend.snapshot(state.get('sessions',[])).get('connected'): return False
        if turn.get('brainThread') and turn['brainThread']!=o.backend.data.get('thread'): return False
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
        remote=o.backend.data.get('requests',{}).get(identity,{}) if o.backend else {}
        if remote.get('turn'):
            d['brainTurn']=remote['turn'];d.setdefault('deliveredAt',o.clock())
        if state == 'retry': d['attempts'] += 1
        if state == 'done':
            d['brainTurn'] = turn['brainTurn']
            if d['brainTurn']:d.setdefault('deliveredAt',o.clock())
            for activity in o._rows('activities'):
                if activity['id']=='failure:'+turn['id']:
                    activity['resolved']=True; o._save('activities',activity)
        o._save('work_deliveries',d)
        if state=='done' and d.get('explicit'):
            # Preserve the exact final event while it is at the timeline head;
            # later receipt lookups need not depend on the latest-page window.
            o.work_report_status(identity)

    def metrics(self):
        o = self.owner
        with o.lock:
            kinds = {kind:count for kind,count in o.db.execute("SELECT json_extract(data,'$.kind'),COUNT(*) FROM work_events GROUP BY json_extract(data,'$.kind')")}
            important=o.db.execute("SELECT COUNT(*) FROM work_events WHERE json_extract(data,'$.notify')=1").fetchone()[0]
            deliveries=o._rows('work_deliveries')
            requests=o.backend.data.get('requests',{}) if o.backend else {}
            return {'events':sum(kinds.values()), 'kinds':kinds, 'important':important,
                    'progressWithoutModelTurn':kinds.get('progress',0),
                    'pendingReportEvents':o.db.execute('SELECT COUNT(*) FROM work_delivery_events WHERE delivery IS NULL').fetchone()[0],
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
    def _report_work(self, session, thread):
        """Use the exact live binding; never infer a worker from its directory."""
        from brain_targets import conversation_target
        from brain_observer import observer_id
        state=self.get_state()
        matches=[s for s in state.get('sessions',[]) if s['id']==session]
        s=matches[0] if len(matches)==1 else None
        target=conversation_target(s) if s else None
        if (not target or target['thread']!=thread or not s.get('bindingGeneration') or
                s.get('windowVerified') is not True or not s.get('alive',True)):
            raise ValueError('回報需要原工作存活視窗、精確 thread 與綁定世代。')
        identity=observer_id(self.observer.normalize(target))
        r=next((r for r in self.observer.records() if r['id']==identity),None)
        if not r or not r['active']:raise ValueError('原工作未登錄或已結案。')
        self.observer._read(r,state)
        if r['coverage']!='current' or r.get('runtimeCoverage')!='confirmed':
            raise ValueError('原工作來源或視窗尚未確認，沒有送出回報。')
        return r,s

    def work_report_context(self, data):
        if set(data)!={'session','thread'}:raise ValueError('回報查詢需要精確 session 與 thread。')
        with self.lock,self.db:
            r,s=self._report_work(data['session'],data['thread'])
            events=[json.loads(row[0]) for row in self.db.execute(
                "SELECT data FROM work_events WHERE observer=? AND json_extract(data,'$.kind') IN ('result','progress','error') ORDER BY revision DESC LIMIT 12",(r['id'],))]
            return {'reportOnly':True,'work':r['id'],'session':s['id'],'thread':r['target']['thread'],
                'generation':s['bindingGeneration'],'instruction':r.get('instruction'),
                'sources':events,'receipts':[self.work_report_status(d['id']) for d in self._rows('work_deliveries')
                    if d.get('explicit') and d.get('work')==r['id']]}

    def work_report_status(self, identity):
        with self.lock,self.db:
            d=next((d for d in self._rows('work_deliveries') if d['id']==identity and d.get('explicit')),None)
            if not d:raise ValueError('找不到正式明確回報收據。')
            remote=self.backend.data.get('requests',{}).get(identity,{}) if self.backend else {}
            turn=d.get('brainTurn') or remote.get('turn','')
            if d['state']=='done' and turn and not d.get('summary') and self.backend and self.backend.data.get('thread')==d['thread']:
                try:page=self._timeline()
                except (OSError,ValueError):page={}
                final=next((m for m in reversed(page.get('messages',[])) if m.get('turn')==turn and
                    m.get('role')=='assistant' and m.get('phase')=='final_answer' and m.get('text','').strip()),None)
                if final and page.get('thread')==d['thread']:
                    d['summary']={k:final.get(k) for k in ('id','cursor','turn')};self._save('work_deliveries',d)
            return {k:d.get(k) for k in ('id','work','session','generation','instruction','sources','requestID','state','created','updated','deliveredAt','attempts','error')} | {
                'ok':True,'reportOnly':True,'brainThread':d['thread'],'workerThread':d['workerThread'],
                'brainTurn':turn,'delivered':bool(turn),'summarized':bool(d.get('summary')),'summary':d.get('summary')}

    def request_work_report(self, data):
        required={'requestID','session','thread','generation','work','instruction','sources','confirmUserRequested'}
        if set(data)!=required or data['confirmUserRequested'] is not True:
            raise ValueError('明確回報需要本人指令、原結果來源及確認；不能以專員文字代替本人授權。')
        if any(not isinstance(data[k],str) or not data[k] for k in required-{'sources','confirmUserRequested'}):
            raise ValueError('回報識別欄位無效。')
        sources=data['sources']
        if (not isinstance(sources,list) or not 1<=len(sources)<=12 or
                any(not isinstance(s,str) for s in sources) or len(set(sources))!=len(sources)):
            raise ValueError('回報需要 1 至 12 筆不重複原結果來源。')
        with self.lock,self.db:
            fingerprint,old=self._receipt(data)
            # Exact retries remain readable after window closure or new input.
            if old:return self.work_report_status(old['id'])
            identity='explicit-report:'+digest([data['work'],data['instruction']])[:40]
            previous=next((d for d in self._rows('work_deliveries') if d['id']==identity),None)
            if previous:
                if (previous['sources']!=sorted(sources) or previous['session']!=data['session'] or
                        previous['workerThread']!=data['thread'] or previous['generation']!=data['generation']):
                    raise ValueError('此本人指令已有回報收據；不可變更來源或重複通知。')
                return self._record(data,fingerprint,self.work_report_status(identity))
            r,s=self._report_work(data['session'],data['thread'])
            if r['id']!=data['work'] or s['bindingGeneration']!=data['generation']:
                raise ValueError('回報不屬於原工作或綁定世代已變更。')
            instruction=self.observation_source(r['id'],data['instruction'])['event']
            if (instruction['kind']!='instruction' or instruction['source'].get('provenance') not in {None,'user'} or
                    r.get('instruction',{}).get('event')!=instruction['id']):
                raise ValueError('回報必須引用原工作目前的本人指令，不接受專員、交辦或背景通知作新授權。')
            evidence=[self.observation_source(r['id'],e)['event'] for e in sources]
            if any(e['kind'] not in {'result','progress','error'} or e['source'].get('provenance') not in {None,'assistant'} for e in evidence):
                raise ValueError('回報只接受此工作的原專員結果。')
            events=[instruction,*evidence]
            if any(e['source'].get('thread')!=data['thread'] or e['source'].get('revision')!=r['sourceRevision'] for e in events):
                raise ValueError('回報來源 thread 或來源版本不一致。')
            thread=self.backend.data.get('thread') if self.backend else None
            if not thread:raise ValueError('尚無原 Brain 對話，沒有建立或改送其他對話。')
            now=self.clock()
            d={'id':identity,'explicit':True,'work':r['id'],'session':s['id'],'generation':data['generation'],
                'workerThread':data['thread'],'thread':thread,'instruction':instruction['id'],'sources':sorted(sources),
                'requestID':data['requestID'],'events':events,'through':r['revision'],
                'state':'queued','created':now,'updated':now,'attempts':0,'error':''}
            self._save('work_deliveries',d)
            self._save('turns',{'id':identity,'goal':0,'topic':'','state':'queued','brainThread':thread,
                'trigger':{'kind':'explicit_report','delivery':identity,'reportOnly':True,
                    'title':'本人明確要求的工作回報','instruction':instruction['id']}})
            self._set('enabled',True)
            return self._record(data,fingerprint,self.work_report_status(identity))

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
            previous_wait=r.get('semanticWaiting') or r.get('sourceWaiting')
            if wait=='resolved' and previous_wait:
                previous=previous_wait['revision']
                newest=max(self.db.execute('SELECT revision FROM work_events WHERE id=?',(s,)).fetchone()[0] for s in sources)
                if newest<=previous: raise ValueError('解除待本人需要較新的來源證據。')
            answered=(previous_wait or {}).get('answeredBy')
            instruction=r.get('instruction') or {}
            if answers_wait(instruction.get('text',''),{}):answered=instruction.get('event') or answered
            # The human can answer before the first summary is written, too.
            if wait=='pending' and answered:
                answer_revision=self.db.execute('SELECT revision FROM work_events WHERE id=?',(answered,)).fetchone()[0]
                renewed=any(e['kind'] in {'result','error','waiting'} and
                    self.db.execute('SELECT revision FROM work_events WHERE id=?',(e['id'],)).fetchone()[0]>answer_revision
                    for e in evidence)
                if not renewed: raise ValueError('本人已回答；重新建立等待需要較新的問題或錯誤來源。')
            identity='summary:'+digest(data)
            self.observer.event(r,identity,'summary',notify=False,source={'events':sources,'brainTurn':data['turn']})
            r['summary']={'fields':summary,'sources':sources,'brainTurn':data['turn'],'revision':r['revision']}
            if wait=='pending': r['semanticWaiting']={'id':identity,'kind':'semantic','text':summary['blocker'], 'source':sources,'revision':r['revision']}
            elif wait=='resolved': r['semanticWaiting']=None; r['sourceWaiting']=None
            self.observer.save(r)
            return self._record(data,fingerprint,{'ok':True,'reportOnly':True,'work':r['id'],'revision':r['revision']})
