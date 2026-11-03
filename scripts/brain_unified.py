"""One authoritative Brain transcript; durable task/action/outbox projections.

No transcript replay can execute an action. All actions cross an authenticated,
exact active-turn contract and an atomic receipt transaction before native IO.
"""
import hashlib
import json
from pathlib import Path
import shlex
import sys
import uuid

from brain_codex import BrainPending
from brain_provider import plan_schema, validate_plan
from brain_service import BrainService, ACTIVE, conversation_id, running, waiting
from brain_tools import instructions
from brain_reports import ReportingAPI, WorkReports
from photo_store import clean as clean_photos


class UnifiedBrain(ReportingAPI, BrainService):
    queue_same_thread = True
    reference_topics_only = True
    def __init__(self, *args, timeline, close_work=None, ack_native=None, observe_page=None, observe_target=None, closure_status=None, **kwargs):
        self.read_timeline, self.close_work, self.ack_native = timeline, close_work, ack_native
        self.closure_status = closure_status
        super().__init__(*args, **kwargs)
        with self.lock, self.db:
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS action_contexts(id TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS activities(id TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS closings(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            ''')
        command = shlex.join([sys.executable, '-B', str(Path(__file__).with_name('brain_tools.py'))])
        self.tool_instructions = instructions(command)
        from brain_observer import WorkObserver
        self.observer = WorkObserver(self, observe_page, observe_target) if observe_page else None
        self.reports = WorkReports(self) if self.observer else None
        from brain_attention_store import AttentionStore
        self.attention = AttentionStore(self)

    def _timeline(self, **options):
        if not self.backend or not self.backend.data.get('thread'):
            return {'messages': [], 'hasMore': False, 'cursor': 0, 'latestCursor': 0, 'thread': ''}
        return self.read_timeline(self.backend.data, **options)

    def _context(self, turn, state):
        if turn['trigger'].get('delivery'): return self.reports.source(turn)
        reference=''
        if turn['trigger']['kind']=='user':
            reference=turn.get('referenceTopic') or turn.get('topic','')
            turn.update(topic='',referenceTopic=reference)
        source = super()._context(turn, state)
        if reference:
            source['referenceTopic']=self._topic(reference)
            if reference not in {t['id'] for t in source['topics']}:
                source['topics']=[*source['topics'][:79],source['referenceTopic']]

        source.update(natural=True, conversation=[], memory=[])
        if turn.get('brainThread'): source['brainThread']=turn['brainThread']
        if getattr(self,'photos',None) and self.backend.data.get('thread'):
            source.update(availablePhotos=self.photos.catalog(self.photo_scope()),photoScope=self.photo_scope())
        if turn.get('photos'):
            if turn.get('brainThread')!=self.backend.data.get('thread'):raise ValueError('照片所屬Brain已變更，沒有改送。')
            source.update(photos=turn['photos'],brainThread=turn['brainThread'])
        if self.observer and turn['trigger']['kind']=='user':
            source['works']=self.observer.context_works()
        if self.observer and turn['trigger']['kind']!='user':
            source.update(reportOnly=True, dispatchBudget=0, sessions=[],targets=[],releasableJobs=[])
        # The actual dialogue is already in the thread; task metadata only.
        for topic in source['topics']: topic.pop('preview', None)
        if turn['trigger']['kind']=='result':
            related={j['session'] for j in source['jobs']}
            source['sessions']=[s for s in source['sessions'] if s['id'] in related]
            original=next((j for j in self._rows('jobs') if j['id']==turn['trigger'].get('job')),None)
            stale=bool(original and original.get('authorityGoal') and original['authorityGoal']!=source['scope']['goal'])
            source['authorityStale']=stale
            if stale or turn['trigger'].get('closing'):
                source.update(dispatchBudget=0,sessions=[],targets=[],releasableJobs=[])
        return source

    def submit(self, data):
        # The inherited durable user row is an outbox command/authority record,
        # never served as a second chat. It is visible as a pending receipt until
        # the actual user event arrives from the thread.
        return {**super().submit(data), 'state': 'queued', 'chatSource': 'brain-thread'}

    def _activity(self, text, *, topic='', kind='status', identity=None, job=''):
        identity = identity or str(uuid.uuid4())
        self._save('activities', {'id': identity, 'text': text, 'topic': topic, 'kind': kind,
                                 'job': job, 'created': self.clock()})
        return identity

    def _report(self, text, *, topic='', kind='result', job=''):
        return self._activity(text, topic=topic, kind=kind, job=job)

    def _finish(self, job, state, text):
        if job['state'] == state and job.get('detail') == text: return
        job.update(state=state, detail=text, finished=self.clock(), review='pending')
        if state == 'completed': job['result'] = text
        self._save('jobs', job)
        self._event(job, state, text)

    def _event(self, job, kind, text):
        # Only an actual delegated outcome warrants model work. Human waits,
        # uncertain transport and manual discussion are deterministic state/cards.
        # One job outcome owns both reporting and attention, independent of when
        # its transcript is indexed. The observer cannot create a second turn.
        if kind not in {'completed','blocked'} or job.get('manualObservation'):
            job.update(review='direct'); self._save('jobs',job)
            return
        if job.get('outcomeReport'):
            return
        # A second real wait with identical text is a different transition.
        sequence = job.get('eventSequence', 0) + 1
        job['eventSequence'] = sequence
        identity = 'event:' + hashlib.sha256((job['id'] + str(sequence) + kind + text).encode()).hexdigest()[:40]
        if any(t['id'] == identity for t in self._rows('turns')): return
        self._save('turns', {'id': identity, 'goal': job['goal'], 'topic': job.get('topic', ''), 'state': 'queued',
            'brainThread':job.get('brainThread') or self.backend.data.get('thread'),
            'trigger': {'kind': 'result', 'outcome': kind, 'job': job['id'], 'title': job['title'],
                        'result': text[:12000], 'partial': len(text) > 12000, 'reportOnly':bool(self.observer)}})
        job.update(review='pending',reviewEvent=identity,outcomeReport=identity);self._save('jobs',job)
        self._activity(text, topic=job.get('topic', ''), kind=kind, identity=identity, job=job['id'])

    def _plan_one(self, state):
        with self.lock, self.db:
            queued = [t for t in self._rows('turns') if t['state'] in {'queued', 'codex_wait'}]
            queued = [t for t in queued if t.get('retryAt',0)<=self.clock() and
                      (not t['trigger'].get('reportOnly') or self.reports.allowed(t,state))]
            turn = next((t for t in queued if t['state'] == 'codex_wait'), queued[0] if queued else None)
            if not turn: return
            job=next((j for j in self._rows('jobs') if j['id']==turn['trigger'].get('job')),None)
            quiet=(turn['trigger']['kind']=='observation' or bool(turn['trigger'].get('job') and
                (turn['trigger'].get('outcome') not in {'completed','blocked'} or not job or
                 job.get('manualObservation') or job['state']!=turn['trigger'].get('outcome'))))
        try:
            if not self.backend: raise ValueError('Brain 尚未接通。')
            try:self.backend.ensure_tools(self.tool_instructions)
            except (ValueError,OSError):raise BrainPending() from None
            with self.lock, self.db:
                source = turn.get('context') or self._context(turn, state)
                source.update(natural=True,conversation=[],memory=[])
                turn.update(context=source, state='codex_wait', quiet=quiet)
                self._save('turns', turn)
                if self.reports: self.reports.update(turn,'running')
            result = self.backend.retire(source) if quiet else self.backend(source)
            with self.lock, self.db:
                turn.update(state='done', brainTurn=result['turn']); turn.pop('context', None)
                if quiet: turn['quiet']=True
                self._save('turns', turn)
                if self.reports: self.reports.update(turn,'done')
                jobid = turn['trigger'].get('job')
                if jobid:
                    job = next((j for j in self._rows('jobs') if j['id'] == jobid), None)
                    if job and job.get('reviewEvent',turn['id'])==turn['id']:
                        job.update(review='direct' if quiet else 'reported', reviewTurn=result['turn']); self._save('jobs', job)
        except BrainPending: pass
        except Exception as error:
            message = str(error) if type(error) is ValueError else 'Brain 暫時無法整理，原結果與派工收據已保留。'
            with self.lock, self.db:
                record=self.backend.data.get('requests',{}).get(turn['id'],{}) if self.backend else {}
                retry=bool(turn['trigger'].get('reportOnly') and record.get('state')!='failed')
                attempts=turn.get('retryCount',0)+1
                turn.update(state='codex_wait' if retry else 'failed', error=message,
                            retryCount=attempts, retryAt=self.clock()+min(300,2**min(attempts,8)))
                self._save('turns', turn)
                if self.reports:self.reports.update(turn,'retry' if retry else 'failed',message)
                self._activity(message, topic=turn.get('topic', ''), kind='error', identity='failure:'+turn['id'], job=turn['trigger'].get('job',''))

    def action_context(self, data):
        if set(data) != {'thread'} or data['thread'] != self.backend.data.get('thread'):
            raise ValueError('工具不屬於目前 Brain 原對話。')
        active = self.backend.current_turn()
        if active.get('status') != 'inProgress': raise ValueError('動作只能由正在執行的 Brain 回合提出。')
        human = self._human_text(active)
        authority = 'authority:' + hashlib.sha256((active['id']+human).encode()).hexdigest()[:40]
        identity = 'tool:' + str(uuid.uuid4())
        with self.lock, self.db:
            recorded = next((key for key, r in self.backend.data['requests'].items() if r.get('turn') == active['id']), None)
            turn = next((t for t in self._rows('turns') if t['id'] == recorded), None)
            if turn and human:
                row=self.db.execute('SELECT text FROM messages WHERE id=?',(turn['goal'],)).fetchone()
                if turn['trigger']['kind']!='user' or not row or row['text']!=human:turn=None
            if turn:
                turn = {**turn, 'id': identity, 'state': 'tool'}
            else:
                text = human
                if not text.strip() or text.startswith('[Pilot 工作事件 '): raise ValueError('無法確認本輪本人交代，沒有派工。')
                goal = self._get(authority)
                if not goal:
                    goal = self._message(text, role='user', kind='authority', request=authority)
                    self._set(authority,goal)
                turn = {'id': identity, 'goal': goal, 'topic': '', 'state': 'tool', 'trigger': {'kind':'user'}}
            state = self.get_state()
            source = self._context(turn, state)
            value = {'id': identity, 'thread':data['thread'], 'turnID':active['id'], 'turn':turn,
                     'source':source, 'snapshot':state, 'created':self.clock(), 'humanFingerprint':hashlib.sha256(human.encode()).hexdigest()}
            self._save('action_contexts', value)
            self._set('enabled', True)
            return self._public_context(value)

    @staticmethod
    def _human_text(turn):
        return clean_photos('\n'.join(p.get('text','') for i in turn.get('items',[]) if i.get('type')=='userMessage'
                         for p in i.get('content',[]) if p.get('type')=='text' and not p.get('text','').startswith('[Pilot 工作事件 ')))

    def _public_context(self, value):
        schema = plan_schema(value['source'])
        choices=[p['id'] for p in value['source'].get('availablePhotos',[])]
        for kind in ('dispatches','launches'):
            if choices:schema['properties'][kind]['items']['properties']['photos']={'type':'array','items':{'type':'string','enum':choices},'maxItems':4,'uniqueItems':True}
        schema['properties'].pop('reply'); schema['required'].remove('reply')
        topic = schema['properties']['topics']['items']
        topic['properties'].pop('reply'); topic['required'].remove('reply')
        choices = [s['id'] for s in value['source']['sessions']]
        if choices and value['source']['dispatchBudget']:
            schema['properties']['dispatches']['items']['properties']['session']['enum'] = choices
            schema['properties']['dispatches']['maxItems'] = min(4,value['source']['dispatchBudget'])
        schema['properties']['close'] = {'type':'array','maxItems':4,'items':{'type':'string','enum':choices or ['no-target']}}
        if value['source'].get('reportOnly'):
            for name in ('topics','dispatches','launches','release','close'): schema['properties'][name]['maxItems']=0
        return {'contextID': value['id'], 'turn':value['turnID'], 'scope':value['source']['scope'],
                'reportOnly':value['source'].get('reportOnly',False), 'works':value['source'].get('works',[]),
                'referenceTopic':value['source'].get('referenceTopic'), 'actionSchema':schema, **{k:value['source'][k] for k in ('topics','sessions','targets','jobs','releasableJobs','dispatchBudget')}}

    def apply_actions(self, data):
        if set(data) != {'requestID','contextID','turn','thread','actions'}: raise ValueError('動作欄位不正確。')
        with self.lock, self.db:
            fingerprint, old = self._receipt(data)
            if old: return old
            context = next((x for x in self._rows('action_contexts') if x['id'] == data['contextID']), None)
            if not context or context['thread'] != data['thread'] or context['turnID'] != data['turn'] or data['thread'] != self.backend.data.get('thread'):
                raise ValueError('動作來源不是原 Brain 回合。')
            current = self.backend.current_turn()
            if current['id'] != data['turn'] or current.get('status') != 'inProgress': raise ValueError('原 Brain 回合已結束，不能重播動作。')
            if hashlib.sha256(self._human_text(current).encode()).hexdigest()!=context['humanFingerprint']:
                raise ValueError('本人在原回合補充了指令，請重新讀取 context；舊動作未送出。')
            actions = data['actions']
            if context['source'].get('reportOnly') and (not isinstance(actions,dict) or any(actions.values())):
                raise ValueError('工作觀察回合僅可整理回報；不能派工、改授權、解除交辦或關窗。')
            if not isinstance(actions, dict) or set(actions) - {'topics','dispatches','launches','release','close'} or not {'topics','dispatches','launches','release'} <= set(actions): raise ValueError('動作必須使用本輪契約。')
            if any(not isinstance(actions[k],list) for k in ('topics','dispatches','launches','release')): raise ValueError('動作集合必須是陣列。')
            if any(not isinstance(t,dict) or set(t) != {'id','title','goal','state'} for t in actions['topics']): raise ValueError('任務欄位不正確。')
            closes = actions.get('close',[])
            if not isinstance(closes,list) or len(closes)>4 or any(not isinstance(x,str) for x in closes) or len(set(closes))!=len(closes): raise ValueError('關窗資料不正確。')
            close_records=[]
            for session in closes:
                if context['turn']['trigger']['kind']!='user' or not self.close_work: raise ValueError('只有本人交代可以關閉工作視窗。')
                current_session=next((s for s in self.get_state()['sessions'] if s['id']==session),None)
                snapshot=next((s for s in context['snapshot']['sessions'] if s['id']==session),None)
                if not current_session or not snapshot or current_session.get('binding')!=snapshot.get('binding') or conversation_id(current_session)!=conversation_id(snapshot): raise ValueError('關窗目標已改變。')
                if running(current_session) or waiting(current_session): raise ValueError('工作仍在執行或等待核准，沒有關閉。')
                if any(j['session']==session and j['state'] in ACTIVE for j in self._rows('jobs')): raise ValueError('原工作還有交辦，沒有關閉。')
                if any(isinstance(a,dict) and a.get('session')==session for a in actions['dispatches']): raise ValueError('不能同時關閉與派工。')
                target=next((s.get('targetID') for s in context['source']['sessions'] if s['id']==session),None)
                if any(isinstance(a,dict) and a.get('target')==target for a in actions['launches']): raise ValueError('不能同時關閉與開啟同一個對話。')
                preview=self.close_work({'session':session,'binding':current_session['binding']},preview=True)
                if preview.get('bindings')!=[current_session['binding']]: raise ValueError('這個視窗包含其他工作，請由本人在工作管理確認關閉。')
                close_records.append({'id':'close:'+data['requestID']+':'+session,'session':session,'binding':current_session['binding'],'thread':conversation_id(current_session),'generation':current_session.get('bindingGeneration',''),'state':'queued','topic':context['turn'].get('topic',''),'goal':context['turn']['goal'],'title':current_session.get('title','原工作')})
            if any(not isinstance(a,dict) or a.get('session') not in {s['id'] for s in context['source']['sessions']} for a in actions['dispatches']): raise ValueError('交辦目標不在本回合的任務範圍。')
            if context['source'].get('authorityStale') and any(actions.values()): raise ValueError('本人已更新這件事的目標；舊回報只能統整，不沿用舊授權派工。')
            plan = validate_plan({**{k:v for k,v in actions.items() if k!='close'}, 'reply':'動作收據', 'topics':[{**t,'reply':''} for t in actions['topics']]})
            # Result turns cannot silently rewrite the human authority, even
            # when the worker returns instructions pretending to be the user.
            if context['turn']['trigger']['kind'] != 'user':
                for topic in plan['topics']:
                    if topic['goal'] != self._topic(topic['id'])['goal']: raise ValueError('結果回合不能變更本人授權範圍。')
            for action in plan['dispatches']+plan.get('launches',[]):
                if action.get('photos'):
                    if not set(action['photos'])<=set(p['id'] for p in context['source'].get('availablePhotos',[])):raise ValueError('照片不在本回合正式可引用範圍。')
                    if not action.get('instruction','').strip():raise ValueError('附照片派工需要說明用途。')
                    self.photos.paths(action['photos'],context['source']['photoScope'])
            existing_jobs=self._rows('jobs')
            result = self._apply_plan(plan, dict(context['turn']), context['source'], context['snapshot'], record_reply=False, transaction=False)
            for job in self._rows('jobs'):
                if job['id'] in result['jobs']:
                    topic = self._topic(job.get('topic',''))
                    predecessors=[j for j in existing_jobs if j['session']==job['session'] and j['thread']==job['thread'] and j['state'] in ACTIVE]
                    if predecessors: job['predecessor']=predecessors[-1]['id']
                    job.update(brainThread=data['thread'], brainTurn=data['turn'], authorityGoal=topic['goal'], authorityTurn=context['turn']['goal'])
                    self._save('jobs',job)
            for record in close_records:self._save('closings',record)
            result['closings']=[r['id'] for r in close_records]
            result = self._record(data, fingerprint, {**result, 'brainTurn': data['turn']})
        self.wake.set()
        return result

    def _release_job(self, job):
        self._finish(job, 'manual', '已撤回這筆後續交辦；已送出的工作仍繼續，其他任務不受影響。')

    def control(self, data):
        if data.get('action')!='takeover':return super().control(data)
        if set(data)!={'requestID','action','job'}:raise ValueError('Brain 控制操作不正確。')
        with self.lock,self.db:
            fingerprint,old=self._receipt(data)
            if old:return old
            job=next((j for j in self._rows('jobs') if j['id']==data['job']),None)
            if not job:raise ValueError('找不到這筆交辦。')
            if job['state'] in ACTIVE:self._release_job(job)
            return self._record(data,fingerprint,{'ok':True})

    def manual_send(self, session):
        with self.lock, self.db:
            for job in self._rows('jobs'):
                if job['session'] != session or job['state'] not in ACTIVE: continue
                if job['state'] in {'queued','opening'}:
                    self._finish(job,'manual','你已直接操作，後續排隊交辦已撤回。')
                else:
                    job['manualObservation'] = True; self._save('jobs',job)
                    self._set('manual:'+session,self.clock())

    def manual_intervention(self, job):
        job['manualObservation']=True; self._save('jobs',job)
        self._set('manual:'+job['session'],self.clock())
        self._activity('本人正在直接操作原工作；保留結果追蹤，停止自動續派。',topic=job.get('topic',''),job=job['id'])

    def _advance_closings(self):
        with self.lock,self.db:
            for record in self._rows('closings'):
                if record['state']!='queued':
                    if record['state'] == 'uncertain' and self.closure_status and record.get('generation'):
                        try:
                            proof = self.closure_status(record)
                            if proof.get('reliable') and proof.get('absent') and proof.get('generation') == record['generation']:
                                record.update(state='closed', proof=proof)
                                self._save('closings', record)
                                self._closing_event(record, 'closed', '已補查確認原工作視窗關閉，歷史保留。')
                        except (OSError, ValueError): pass
                    if not any(t['id']=='event:'+record['id']+':'+record['state'] or
                               t['id']=='event:'+record['id'] and t.get('trigger',{}).get('outcome')==record['state']
                               for t in self._rows('turns')):
                        self._closing_event(record,record['state'],'工作視窗已關閉，歷史保留。' if record['state']=='closed' else '關窗結果待確認，沒有重試。')
                    continue
                s=next((s for s in self.get_state()['sessions'] if s['id']==record['session']),None)
                if not s or s.get('binding')!=record['binding'] or conversation_id(s)!=record['thread'] or (record.get('generation') and s.get('bindingGeneration') != record['generation']) or running(s) or waiting(s):
                    record['state']='blocked';self._save('closings',record)
                    self._closing_event(record,'blocked','關窗前工作狀態已改變，沒有關閉。');continue
                record['state']='uncertain';self._save('closings',record);self.db.commit()
                try:
                    result=self.close_work({'requestID':record['id'][:80],'session':record['session'],'binding':record['binding'],'bindings':[record['binding']]})
                    if result.get('closed'):record['state']='closed'
                except (ValueError,OSError):pass
                self._save('closings',record)
                self._closing_event(record,record['state'],'工作視窗已關閉，歷史保留。' if record['state']=='closed' else '關窗結果待確認，沒有重試。')

    def _closing_event(self, record, outcome, text):
        identity='event:'+record['id']+':'+outcome
        self._save('turns', {'id':identity,'goal':record['goal'],'topic':record['topic'],'state':'queued',
            'trigger':{'kind':'result','outcome':outcome,'closing':record['id'],'title':record['title'],'result':text}})
        self._activity(text,kind='status' if outcome=='closed' else 'error',identity=identity)

    def _observe_job(self, job):
        with self.lock:
            before = next(j for j in self._rows('jobs') if j['id']==job['id'])
            if before['state'] not in ACTIVE:return
            # Different sessions in the same checkout are not file isolation.
            # Serialize writes conservatively; independent projects still run.
            if before['state']=='queued':
                if before.get('predecessor'):
                    previous=next(j for j in self._rows('jobs') if j['id']==before['predecessor'])
                    if previous['state'] in ACTIVE:return
                    if previous['state']!='completed':
                        with self.db:self._finish(before,'blocked','前一筆工作未正常完成，後續指令未送出。')
                        return
                    before['baseline']=hashlib.sha256(previous['wire'].encode()).hexdigest()
                    with self.db:self._save('jobs',before)
                directory=(before.get('target') or {}).get('directory')
                if directory and any(s['id'] != before['session'] and s.get('directory') == directory
                                     and (running(s) or waiting(s)) for s in self.get_state().get('sessions', [])):
                    self._queue_wait(before,'checkout_busy','同一專案目錄的其他工作仍在執行或等待核准；尚未派送。')
                    return  # Direct work in the same checkout also owns the files.
                for other in self._rows('jobs'):
                    if other['id']==before['id'] or other['state'] not in ACTIVE: continue
                    if directory and directory==(other.get('target') or {}).get('directory'):
                        if other['state'] not in {'queued','opening'} or (other['created'],other['id']) < (before['created'],before['id']):
                            self._queue_wait(before,'checkout_job','等待同一專案目錄的前一筆交辦確認收據或完成：'+other['id'],other['id'])
                            return
        if before.get('manualObservation'):
            try: thread = self.get_thread(before['session'])
            except (OSError, ValueError): return
            if thread.get('snapshotReliable') is False:return
            s=thread['session']
            if (s.get('binding') != before['binding'] or conversation_id(s)!=before['thread']
                    or (before.get('generation') and s.get('bindingGeneration')!=before['generation'])):
                with self.lock,self.db:self._finish(before,'blocked','原對話綁定已改變，未套用其他視窗結果。')
                return
            if s.get('phase') == 'error':
                with self.lock,self.db:self._finish(before,'blocked','Agent 回報錯誤；手動接手後的段落不當成完成。')
                return
            if waiting(s) or running(s):
                status = 'waiting' if waiting(s) else 'running'
                with self.lock,self.db:
                    changed = before['state'] != status
                    before['state'] = status; self._save('jobs', before)
                    if changed and status == 'waiting': self._event(before, status, '原工作等待本人確認，請使用原始核准卡片。')
                return
            last_user = max((i for i,m in enumerate(thread['messages']) if m['role']=='user'),default=-1)
            replies=[m for m in thread['messages'][last_user+1:] if m['role']=='assistant' and m.get('phase')!='commentary']
            complete = s.get('tool') != 'claude' or (s.get('phase') == 'complete' and s.get('noticeID') and s.get('noticeID') != before.get('noticeBefore'))
            if s.get('phase') not in {'ended','error'} and replies and complete:
                with self.lock,self.db:self._finish(before,'completed',replies[-1]['text'])
            return
        super()._observe_job(job)
        with self.lock,self.db:
            after=next(j for j in self._rows('jobs') if j['id']==job['id'])
            if after['state'] in {'waiting','uncertain'} and after['state']!=before['state']:
                self._event(after,after['state'],after.get('detail') or '原工作等待本人確認，請使用原始核准卡片。')

    def state(self, topic='', before=None, after=None):
        result=super().state('')
        with self.lock:
            selected=self._topic(topic)
            turns=self._rows('turns'); jobs=self._rows('jobs')
            activities=self.attention.activities()
            pending=[]
            for turn in turns:
                if turn['state'] not in {'queued','codex_wait','failed'} or turn['trigger']['kind']!='user': continue
                record=self.backend.data['requests'].get(turn['id'],{}) if self.backend else {}
                if record.get('turn') and turn['state']!='failed': continue
                row=self.db.execute('SELECT text,created FROM messages WHERE id=?',(turn['goal'],)).fetchone()
                if row:pending.append({'id':turn['id'],'text':row['text'],'created':row['created'],'state':turn['state'],'error':turn.get('error','')})
        try: page=self._timeline(before=before,after=after)
        except (OSError,ValueError) as error:
            page={'messages':[],'hasMore':False,'cursor':after or 0,'latestCursor':0,'thread':self.backend.data.get('thread',''),'historyUnavailable':True}
            result['error']='Brain 歷史尚未同步：'+str(error)
        links={}
        for turn in turns:
            r=self.backend.data['requests'].get(turn['id'],{}) if self.backend else {}
            identity=turn.get('brainTurn') or r.get('turn')
            if identity and (turn.get('topic') or turn.get('referenceTopic')):links.setdefault(identity,set()).add(turn.get('topic') or turn['referenceTopic'])
        for job in jobs:
            if job.get('brainTurn') and job.get('topic'):links.setdefault(job['brainTurn'],set()).add(job['topic'])
        page['messages']=[{**m,'links':sorted(links.get(m.get('turn'),[]))} for m in page['messages']]
        try: recent=self._timeline()['messages']
        except (OSError,ValueError):recent=page['messages']
        read=self._get('timelineRead',0); activity_read=set(self._get('activityRead',[]))
        policies=self.attention.policies()
        for topic_summary in result['topics']:
            related=[m for m in recent if topic_summary['id'] in links.get(m.get('turn'),set())]
            topic_summary['unread']=any(m['role']=='assistant' and m.get('phase')=='final_answer' and m['cursor']>read and
                self.attention.unread_policy(policies.get(m.get('turn'),{'notify':True,'attentionIDs':['brain-turn:'+m['turn']]})) for m in related) or any(
                a['topic']==topic_summary['id'] and a.get('notificationEligible') and a['id'] not in activity_read and
                not self.attention.is_read(self.attention.activity_id(a)) for a in activities)
            topic_summary['preview']=related[-1]['text'][:160] if related else topic_summary.get('goal','')[:160]

        result.update(page,unified=True,topic=selected,pending=pending,activities=activities,
                      unreadThrough=self._get('timelineRead',0),chatSource='brain-thread',
                      working=any(t['state'] in {'queued','codex_wait'} for t in turns) or result.get('backend',{}).get('phase') in {'running','starting'})
        if self.observer:
            observed=[self.observer.project(r) for r in self.observer.records() if r['active']]
            result['workWaiting']=[{'work':r['id'],'title':r['title'],'session':r.get('session'),
                'generation':r.get('bindingGeneration'), 'coverage':r['coverage'], **w} for r in observed for w in r['waiting']]
            result['observationCoverage']=self.observer.snapshot(limit=1)['coverage']
        for j in result['jobs']:
            original=next(x for x in jobs if x['id']==j['id'])
            if original.get('review')=='pending' and original['state']=='completed':j['label']='已收到成果，等待 Brain 整理'
        return result

    def acknowledge(self,data):
        if set(data)-{'through','topic','noticeID','activities'} or type(data.get('through')) is not int or data['through']<0: raise ValueError('已讀游標無效。')
        page=self._timeline()
        activity_ids=data.get('activities',[])
        if not isinstance(activity_ids,list) or len(activity_ids)>100 or any(not isinstance(x,str) for x in activity_ids):raise ValueError('狀態已讀識別無效。')
        with self.lock,self.db:
            self._set('timelineRead',max(self._get('timelineRead',0),min(data['through'],page['latestCursor'])))
            valid={a['id'] for a in self._rows('activities')}
            self._set('activityRead',list(set(self._get('activityRead',[])) | (set(activity_ids)&valid)))
            self.attention.acknowledge(page,data['through'],set(activity_ids)&valid)
        if self.ack_native and data.get('noticeID'):
            latest=next((m for m in reversed(page['messages']) if m.get('phase')=='final_answer'),None)
            session=next((s for s in self.get_state()['sessions'] if s['id']==self.backend.session_id),None)
            if latest and session and latest['cursor']<=data['through'] and session.get('lastTurn')==latest['turn'] and session.get('noticeID')==data['noticeID']:
                self.ack_native({'session':session['id'],'noticeID':data['noticeID']})
        return {'ok':True}

    def notification_state(self,native):
        # One conversation notification. Progress alone does not request attention.
        result={**native,'sessions':[dict(s) for s in native.get('sessions',[])]}
        original_unread={s['id']:s.get('unread') for s in native.get('sessions',[])}
        result['sessions']=[{**s,'sourceUnread':original_unread.get(s['id'])} for s in result['sessions'] if not s['id'].startswith('brain:') and s['id']!='brain']
        try:page=self._timeline()
        except (OSError,ValueError):return self.attention.project(result,native,{'messages':[]})
        latest=None; policy=None; pending_sources=set()
        policies=self.attention.policies()
        for message in reversed(page['messages']):
            if message['role']!='assistant' or message.get('phase')!='final_answer':continue
            candidate=policies.get(message['turn'], {'notify':True,'noticeID':'brain-turn:'+message['turn'],
                                                    'attentionIDs':['brain-turn:'+message['turn']]})
            if candidate is not None and not candidate['notify']:continue
            if candidate and message['cursor']>self._get('timelineRead',0):
                pending_sources.update(candidate['attentionIDs'])
            if latest is None:latest,policy=message,candidate
        if policy and pending_sources:
            # A slow push poll must not lose an earlier finished work when a
            # second work replies first. Per-source receipts still dedupe both.
            from brain_observer import digest
            policy={**policy,'attentionIDs':sorted(pending_sources),
                    'noticeID':'observation:'+digest(sorted(pending_sources))[:40]}
        # Native and Brain representations of an observed source share one
        # persistent push identity. Native formal waits/errors stay immediate.
        if self.observer:
            with self.lock:
                records={r.get('session'):r for r in self.observer.records() if r.get('coverage')=='current' and self.observer.fresh(r)}
            for session in result['sessions']:
                record=records.get(session['id'])
                if not record:continue
                if record.get('binding')!=session.get('binding'):continue
                if record.get('bindingGeneration') and record['bindingGeneration']!=session.get('bindingGeneration',session.get('binding')):continue
                event=record.get('latest') or {}
                source_turn=event.get('source',{}).get('turn')
                same_turn=bool(source_turn and source_turn in {session.get('activeTurn'),session.get('lastTurn')})
                # Claude Stop has no native turn ID. A fully-read, exact-thread
                # transcript final before that same Stop is a source fence;
                # later text, a new human message, stale coverage or a reused
                # binding cannot inherit this notice.
                source=event.get('source',{})
                claude_stop=bool(session.get('tool')=='claude' and session.get('phase')=='complete'
                    and source.get('representation')=='claude' and source.get('phase')=='final_answer'
                    and source.get('thread')==session.get('claudeSessionID')
                    and source.get('created') and source['created']<=session.get('updatedAt',0)
                    and source.get('cursor')==record.get('cursor')
                    and record.get('runtime',{}).get('noticeID')==session.get('noticeID'))
                if session.get('phase') in {'waiting','error'}:
                    notice=(record.get('waiting') or {}).get('id') if session['phase']=='waiting' else record.get('runtime',{}).get('event')
                    session['attentionIDs']=[notice] if notice else []
                    if same_turn and event.get('kind') in {'result','error'}:
                        session['attentionIDs'].append(event['event'])
                elif session.get('phase')=='complete' and (claude_stop or source_turn and source_turn==session.get('lastTurn')):
                    if event['kind'] in {'progress','tool_notification','status'}:session['notificationSuppressed']=True
                    elif event['kind'] in {'result','error'}:session['attentionIDs']=[event['event']]

        if latest:
            original=next((s for s in native.get('sessions',[]) if s['id']==self.backend.session_id),None)
            if original and original.get('lastTurn')==latest['turn'] and not original.get('unread'):
                with self.lock,self.db:self._set('timelineRead',max(self._get('timelineRead',0),latest['cursor']))
            with self.lock:
                dispatching=any(j.get('brainTurn')==latest['turn'] and j['state'] in ACTIVE for j in self._rows('jobs'))
            if dispatching:latest=None
        if latest:
            result['sessions'].append({'id':'brain','title':'Brain','status':'有回覆需要查看','phase':'complete',
                'unread':latest['cursor']>self._get('timelineRead',0),'noticeID':policy['noticeID'] if policy else str(latest['cursor']),
                'attentionIDs':policy['attentionIDs'] if policy else [],'noticeAt':latest['created']})
        with self.lock:
            unread=[a for a in self.attention.activities() if a.get('notificationEligible') and
                    a['id'] not in self._get('activityRead',[]) and not self.attention.is_read(self.attention.activity_id(a))]
        if unread:
            a=unread[-1]
            existing=next((s for s in result['sessions'] if s['id']=='brain' and s.get('unread')), {})
            result['sessions']=[s for s in result['sessions'] if s['id']!='brain']
            from brain_observer import digest
            ids=sorted(set([self.attention.activity_id(value) for value in unread]+existing.get('attentionIDs',[])))
            key=ids[0] if len(ids)==1 else 'attention:'+digest(ids)[:40]
            result['sessions'].append({'id':'brain','title':'Brain','status':a['text'],'phase':'error' if a['kind']=='error' else 'waiting','noticeReason':'user_input','unread':True,'noticeID':key,'attentionIDs':ids,'noticeAt':max(a['created'],existing.get('noticeAt',0))})
        return self.attention.project(result,native,page)

    def tick(self):
        # Direct TUI turns also get the formal tool contract. Never interrupts an
        # active turn; first installation waits for the existing turn to finish.
        try:self.backend.ensure_tools(self.tool_instructions)
        except (ValueError,OSError):pass
        if self.observer:
            self.observer.reconcile()
            self.reports.stage()
            self.attention.health()
        self._advance_closings()
        super().tick()
