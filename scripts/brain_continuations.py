"""Finite closeout under the owner's local-machine trust policy.

New, current local user instructions need no second native consent. This is not
proof of human identity: local transcript/store tampering is outside this mode.
Worker, dispatch and tool content remain evidence, never user instructions.
Legacy consent receipts are preserved without promotion or replay.
"""
import copy
import threading
from brain_observer import digest
from brain_service import ACTIVE, running, waiting


def consent_text(grant):
    from datetime import datetime
    labels = {'verify_delivery': '核對此工作交付來源', 'close_work': '關閉此工作（須閒置、無核准等待且視窗不含其他工作）',
              'asana_complete_existing': '完成指定既有 Asana 任務，移至指定既有月份區段'}
    actions = []
    for action in grant['proposal']['actions']:
        line = labels[action['kind']]
        if action['kind'] == 'asana_complete_existing':
            line += '\n任務：'+action['task']+'／專案：'+action['project']+'／月份：'+action['month']+'／section：'+action['section']
        actions.append(line)
    return ('原訊息（請核對是否符合你的意思）：'+grant['original']['text']+'\n\n工作：'+grant['work']+
            '\n對話：'+grant['workerThread']+'\n執行時機：'+('等待新結果後' if grant['proposal']['when'] == 'result' else '核對目前結果後')+
            '\n到期：'+datetime.fromtimestamp(grant['expires']).astimezone().isoformat(timespec='seconds')+
            '\n\n同意的動作：\n'+'\n\n'.join(actions))


class Continuations:
    def __init__(self, owner, confirm=None, execute=None):
        self.o, self.confirm, self.execute = owner, confirm, execute
        self.prompt_lock = threading.Lock()
        self.run_lock = threading.Lock()
        with owner.lock, owner.db:
            owner.db.execute('CREATE TABLE IF NOT EXISTS continuations(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            if owner._get('localContinuationSince') is None:
                owner._set('localContinuationSince', owner.clock())
            for g in owner._rows('continuations'):
                if g['state'] == 'confirming':
                    g.update(state='needs_confirmation', error='服務重啟；未完成的本人確認沒有授權。')
                    owner._save('continuations', g)

    def get(self, identity):
        g = next((g for g in self.o._rows('continuations') if g['id'] == identity), None)
        if not g: raise ValueError('找不到接續收據。')
        return g

    def status(self, identity):
        with self.o.lock, self.o.db:
            g = self.get(identity)
            remote = self.o.backend.data.get('requests', {}).get(identity, {}) if self.o.backend else {}
            g['brainTurn'] = g.get('brainTurn') or remote.get('turn', '')
            if g.get('brainTurn') and g.get('modelDone') and not g.get('summary'):
                page = self.o._timeline()
                if page.get('thread') == g['brainThread']:
                    final = next((m for m in reversed(page.get('messages', [])) if
                        m.get('turn') == g['brainTurn'] and m.get('role') == 'assistant' and
                        m.get('phase') == 'final_answer' and m.get('text', '').strip()), None)
                    if final: g['summary'] = {k: final.get(k) for k in ('id', 'turn', 'cursor')}
            self.o._save('continuations', g)
            return copy.deepcopy(g) | {'delivered': bool(g['brainTurn']), 'summarized': bool(g.get('summary')),
                'confirmationURL': '/continuation.html#' + identity}

    @staticmethod
    def actions(values):
        if not isinstance(values, list) or not 1 <= len(values) <= 3:
            raise ValueError('接續只接受 1 至 3 個明列動作。')
        seen = set()
        for a in values:
            if not isinstance(a, dict): raise ValueError('接續動作格式錯誤。')
            kind = a.get('kind')
            keys = {'kind'} if kind in {'verify_delivery', 'close_work'} else {'kind', 'task', 'project', 'section', 'month'}
            if kind not in {'verify_delivery', 'close_work', 'asana_complete_existing'} or set(a) != keys or kind in seen:
                raise ValueError('接續不支援此動作；合併、部署、刪除及正式核准不能轉授。')
            if kind == 'asana_complete_existing':
                import re
                if any(not isinstance(a[k], str) or not re.fullmatch(r'[0-9]{1,30}', a[k]) for k in ('task', 'project', 'section')) or not isinstance(a['month'], str) or not re.fullmatch(r'20[0-9]{2}/(0[1-9]|1[0-2])', a['month']):
                    raise ValueError('Asana 只接受既有 task/project/section ID 與 YYYY/MM。')
            seen.add(kind)
        if values[0]['kind'] != 'verify_delivery' or ('close_work' in seen and values[-1]['kind'] != 'close_work'):
            raise ValueError('接續須先核對交付，關窗只能是最後一步。')

    def fresh(self, g):
        o = self.o
        if o.clock() >= g['expires']: raise ValueError('接續授權已到期。')
        if not o.backend or o.backend.data.get('thread') != g['brainThread']:
            raise ValueError('原 Brain thread 已改變。')
        r, s = o._report_work(g['session'], g['workerThread'])
        if (r['id'] != g['work'] or s['bindingGeneration'] != g['generation'] or
                s.get('binding') != g['binding'] or r['sourceRevision'] != g['sourceRevision']):
            raise ValueError('原工作、來源版本或精確綁定已改變。')
        if r.get('instruction', {}).get('event') != g['instruction']:
            raise ValueError('原工作有較晚指令；舊接續停止，保留視窗。')
        # Any later Brain human turn fences a grant too. Do not guess whether a
        # new instruction is a cancellation by matching a phrase.
        if self.brain_fence() != g['brainFence']:
            raise ValueError('Brain 有較晚本人決定；舊接續停止。')
        return r, s

    def brain_fence(self):
        o = self.o
        latest = max((row[0] for row in o.db.execute("SELECT id FROM messages WHERE role='user'")), default=0)
        requests = o.backend.data.get('requests', {}) if o.backend else {}
        known = {requests.get(t['id'], {}).get('turn'): '[Pilot 工作事件 ' + t['id'] + '] ' +
                 t['trigger'].get('title', '工作') + '有新的回報，請統整原任務結果。'
                 for t in o._rows('turns') if t['trigger']['kind'] != 'user'}
        page = o._timeline()
        decisions = [m for m in page.get('messages', []) if m.get('role') == 'user' and
                     not (m.get('turn') in known and m.get('text') == known[m.get('turn')])]
        thread = o.backend.data.get('thread') if o.backend else ''
        previous = o._get('continuationBrainDecision', {})
        if previous.get('thread') != thread: previous = {'thread': thread, 'cursor': -1}
        if decisions:
            last = decisions[-1]
            if last.get('cursor', 0) >= previous['cursor']:
                previous = {'thread': thread, 'cursor': last.get('cursor', 0),
                            'event': last.get('id'), 'textHash': digest(last.get('text', ''))}
                o._set('continuationBrainDecision', previous)
        # A human event falling outside the bounded latest page is not a new
        # decision. Preserve its watermark across long output and restart.
        return digest([latest, previous])

    def propose(self, data):
        keys = {'requestID', 'session', 'thread', 'work', 'generation', 'instruction', 'sources', 'when', 'actions', 'expiresIn'}
        if not isinstance(data, dict) or set(data) != keys: raise ValueError('接續提案欄位不正確。')
        if any(not isinstance(data[k], str) or not data[k] for k in keys - {'sources', 'actions', 'expiresIn'}):
            raise ValueError('接續識別不正確。')
        self.actions(data['actions'])
        if data['when'] not in {'now', 'result'} or type(data['expiresIn']) is not int or not 60 <= data['expiresIn'] <= 604800:
            raise ValueError('接續時機或有效秒數不正確（60 至 604800）。')
        if not isinstance(data['sources'], list) or len(data['sources']) > 12 or any(not isinstance(s, str) for s in data['sources']) or len(set(data['sources'])) != len(data['sources']) or (data['when'] == 'now' and not data['sources']):
            raise ValueError('立即接續須引用原結果；等待結果的授權可不帶來源。')
        o = self.o
        with o.lock, o.db:
            fingerprint, old = o._receipt(data)
            if old: return self.status(old['id'])
            identity = 'continuation:' + digest([data['work'], data['instruction']])[:40]
            previous = next((g for g in o._rows('continuations') if g['id'] == identity), None)
            scope = {k: v for k, v in data.items() if k != 'requestID'}
            if previous:
                if previous['proposal'] != scope: raise ValueError('此原訊息已有接續提案；不能改範圍或重複執行。')
                return o._record(data, fingerprint, self.status(identity))
            r, s = o._report_work(data['session'], data['thread'])
            event = o.observation_source(data['work'], data['instruction'])['event']
            if (r['id'] != data['work'] or s['bindingGeneration'] != data['generation'] or
                    event['kind'] != 'instruction' or event.get('partial') or
                    event['source'].get('provenance') not in {None, 'user'} or
                    event['source'].get('thread') != data['thread'] or
                    event['source'].get('revision') != r['sourceRevision'] or
                    r.get('instruction', {}).get('event') != data['instruction']):
                raise ValueError('必須引用目前原工作完整指令；派工、工具、文件與專員文字不能充當本人。')
            evidence = self.evidence(r, data['sources'])
            brain = o.backend.data.get('thread') if o.backend else None
            if not brain: raise ValueError('尚無原 Brain thread。')
            g = {'id': identity, 'proposal': copy.deepcopy(scope), 'requestID': data['requestID'],
                'state': 'needs_confirmation', 'work': r['id'], 'session': s['id'], 'binding': s['binding'],
                'workerThread': data['thread'], 'generation': data['generation'], 'instruction': event['id'],
                'sourceRevision': r['sourceRevision'], 'original': event, 'evidence': evidence,
                'actions': [{'scope': copy.deepcopy(a), 'state': 'pending', 'id': identity + ':' + str(i)} for i, a in enumerate(data['actions'])],
                'brainThread': brain, 'brainFence': self.brain_fence(), 'created': o.clock(),
                'expires': o.clock() + data['expiresIn'], 'baselineResult': r.get('result', {}).get('event'),
                'sourceTrust': 'transcript_unverified_requires_native_confirmation', 'error': ''}
            g['digest'] = digest({k: g[k] for k in ('id', 'proposal', 'original', 'binding', 'brainThread', 'brainFence', 'expires')})
            self.local_authorize(g)
            o._save('continuations', g)
            return o._record(data, fingerprint, self.status(identity))

    def local_authorize(self, g):
        """Only new exact instructions; never retroactively approve stored proposals."""
        event = g['original']
        import math
        created = event['source'].get('created')
        if (type(created) not in (int, float) or not math.isfinite(created) or
                created < self.o._get('localContinuationSince') or
                created > self.o.clock() + 5 or self.o.clock() - created > 86400):
            return
        g.update(state='authorized', sourceTrust='owner_local_machine_policy',
                 authorization={'id': 'grant:' + g['digest'], 'digest': g['digest'],
                    'verifiedAt': self.o.clock(), 'method': 'local_user_instruction',
                    'scope': g['proposal']['actions'], 'source': g['instruction'],
                    'humanIdentityVerified': False})

    def from_report(self, delivery):
        """Called only for a NEW explicit report, never by observation or replay.

        This narrow convenience recognizes complete closeout commands, not
        substrings/quotes. Other scoped instructions use continuation-propose.
        It does not submit a report or turn a progress report into authority.
        """
        import re
        text = delivery['events'][0]['text']
        command = re.sub(r'[\s，,。.!！]', '', text).lower()
        if command not in {'跟brain說結案吧', '請跟brain說結案吧', '跟brain說結案',
                '請brain收尾', '讓brain收尾', '做完了跟brain回報並結束工作',
                '做完了跟brain回報結束工作', '做完了回報brain收尾'}:
            return
        import uuid
        identity = 'continuation:' + digest([delivery['work'], delivery['instruction']])[:40]
        g = next((g for g in self.o._rows('continuations') if g['id'] == identity), None)
        if g is None:
            g = self.propose({'requestID': 'report-closeout:' + str(uuid.uuid4()),
            'session': delivery['session'], 'thread': delivery['workerThread'],
            'work': delivery['work'], 'generation': delivery['generation'],
            'instruction': delivery['instruction'], 'sources': delivery['sources'],
            'when': 'now', 'expiresIn': 86400,
            'actions': [{'kind': 'verify_delivery'}, {'kind': 'close_work'}]})
        # Keep the original report-only turn. A different, immutable grant owns
        # execution, and cannot run until the report has actually been delivered.
        stored = self.get(g['id'])
        stored['report'] = delivery['id']
        self.o._save('continuations', stored)
        delivery['continuation'] = g['id']
        self.o._save('work_deliveries', delivery)

    def evidence(self, record, ids):
        events = [self.o.observation_source(record['id'], e)['event'] for e in ids]
        if any(e['kind'] not in {'result', 'progress', 'error'} or e['source'].get('provenance') not in {None, 'assistant'} or e['source'].get('thread') != record['target']['thread'] or
               e['source'].get('revision') != record['sourceRevision'] for e in events):
            raise ValueError('結果不是此工作目前來源版本的專員證據。')
        return events

    def request_confirmation(self, identity):
        if not self.confirm: raise ValueError('此平台沒有原生確認入口；未授權接續。')
        o = self.o
        with o.lock, o.db:
            g = self.get(identity)
            if g['state'] != 'needs_confirmation': return self.status(identity)
            self.fresh(g)
            if not self.prompt_lock.acquire(blocking=False): raise ValueError('已有本人確認視窗，請先完成。')
            g.update(state='confirming', sourceTrust='transcript_unverified_requires_native_confirmation', error='')
            o._save('continuations', g)
        def verify():
            try:
                # Callback is native-only, never a client-supplied attestation.
                proof = self.confirm(copy.deepcopy(g))
                with o.lock, o.db:
                    current = self.get(identity)
                    if current['state'] != 'confirming': return
                    self.fresh(current)
                    if (proof.get('digest') != g['digest'] or proof.get('challenge') != g['id'] or
                            proof.get('method') != 'native_scope_confirmation' or proof.get('approved') is not True):
                        raise ValueError('本人未確認；沒有建立授權。')
                    current.update(state='authorized', authorization={'id': 'grant:' + g['digest'],
                        'digest': g['digest'], 'verifiedAt': o.clock(), 'method': proof['method'],
                        'scope': g['proposal']['actions'], 'source': g['instruction']}, error='')
                    o._save('continuations', current)
            except Exception as error:
                with o.lock, o.db:
                    current = self.get(identity)
                    if current['state'] == 'confirming':
                        current.update(state='needs_confirmation', error=str(error) if isinstance(error, ValueError) else '範圍確認中斷；沒有建立授權。')
                        o._save('continuations', current)
            finally: self.prompt_lock.release()
        threading.Thread(target=verify, daemon=True).start()
        return self.status(identity)

    def revoke(self, identity):
        with self.o.lock, self.o.db:
            g = self.get(identity)
            if g['state'] != 'completed':
                g.update(state='revoked', error='已停止接續；已完成動作不會反向重做。')
                self.o._save('continuations', g)
            return self.status(identity)

    def stage(self):
        o = self.o
        with o.lock, o.db:
            for g in o._rows('continuations'):
                if g['state'] != 'authorized': continue
                try:
                    r, session = self.fresh(g)
                    if g.get('report'):
                        report = next((d for d in o._rows('work_deliveries') if d['id'] == g['report']), {})
                        if report.get('state') != 'done' or not report.get('brainTurn'): continue
                    if running(session) or waiting(session) or any(j.get('session') == g['session'] and j['state'] in ACTIVE for j in o._rows('jobs')): continue
                    if g['proposal']['when'] == 'result':
                        result = r.get('result', {}).get('event')
                        if not result or result == g['baselineResult']: continue
                        g['evidence'] = self.evidence(r, [result])
                    g['state'] = 'queued'; o._save('continuations', g)
                    o._save('turns', {'id': g['id'], 'goal': 0, 'topic': '', 'state': 'queued',
                        'brainThread': g['brainThread'], 'trigger': {'kind': 'authorized_continuation', 'continuation': g['id']}})
                    o._set('enabled', True)
                except ValueError as error:
                    g.update(state='blocked', error=str(error)); o._save('continuations', g)
                    if g.get('jobOutcome') or g.get('report'):
                        o._save('turns', {'id': g['id'], 'goal': 0, 'topic': '', 'state': 'queued',
                            'brainThread': g['brainThread'], 'trigger': {'kind': 'authorized_continuation', 'continuation': g['id']}})

    def claim_outcome(self, job):
        """One verified grant owns the job outcome notification as well."""
        from brain_observer import observer_id
        if not job.get('target') or not self.o.observer: return ''
        work = observer_id(self.o.observer.normalize(job['target']))
        for g in self.o._rows('continuations'):
            if g['work'] != work or g['state'] not in {'authorized', 'queued'}: continue
            try: self.fresh(g)
            except ValueError: continue
            g['jobOutcome'] = job['id']; self.o._save('continuations', g)
            return g['id']
        return ''

    def allowed(self, turn, state):
        g = self.get(turn['trigger']['continuation'])
        return bool(self.o.backend and self.o.backend.data.get('thread') == g['brainThread'] and
                    self.o.backend.snapshot(state.get('sessions', []))['connected'])

    def source(self, turn):
        g = self.get(turn['trigger']['continuation'])
        return {'requestID': g['id'], 'brainThread': g['brainThread'], 'scopeBound': True,
            'scope': {'id': '', 'title': '本人有限收尾', 'goal': ''}, 'userGoal': '',
            'trigger': turn['trigger'], 'continuation': self.status(g['id']), 'reportOnly': False,
            'continuationOnly': True, 'works': [], 'topics': [], 'jobs': [], 'sessions': [], 'targets': [],
            'dispatchBudget': 0, 'releasableJobs': [], 'busySessions': [], 'busyTargets': [],
            'natural': True, 'conversation': [], 'memory': []}

    def update(self, turn, state, error=''):
        identity = turn['trigger'].get('continuation')
        if not identity: return
        g = self.get(identity)
        if state == 'done':
            for job in self.o._rows('jobs'):
                if job['id'] == g.get('jobOutcome'):
                    job.update(review='reported', reviewTurn=turn['brainTurn']); self.o._save('jobs', job)
            g.update(brainTurn=turn['brainTurn'], modelDone=True)
            if g['state'] == 'queued': g.update(state='blocked', error='Brain 已結束回合但未執行正式接續工具。')
        elif state == 'failed': g.update(state='blocked', error=error)
        self.o._save('continuations', g)
        if state == 'done': self.status(identity)

    def execution_check(self, identity):
        o = self.o
        with o.lock, o.db:
            g = self.get(identity)
            if g['state'] != 'queued' or not g.get('authorization'):
                raise ValueError('接續已停止或沒有有效授權。')
            if o._get('paused', False): raise ValueError('Brain 已暫停；沒有接續。')
            active = o.backend.current_turn()
            expected = o.backend.data.get('requests', {}).get(identity, {}).get('turn')
            if not expected or active.get('id') != expected or active.get('status') != 'inProgress':
                raise ValueError('原 Brain 接續回合已改變或中止；沒有執行下一個動作。')
            if o._human_text(active):
                raise ValueError('Brain 本輪收到較晚本人輸入；即使逐字稿尚未保存，也停止舊接續。')
            record, session = self.fresh(g)
            if running(session) or waiting(session) or any(j.get('session') == g['session'] and j['state'] in ACTIVE for j in o._rows('jobs')):
                raise ValueError('工作仍執行、等待正式核准或有未完成交辦；沒有執行收尾。')
            return record, session

    def run(self, data):
        if set(data) != {'thread', 'turn', 'continuation'}: raise ValueError('接續執行只接受原 Brain turn 與收據。')
        o = self.o
        with o.lock, o.db:
            g = self.get(data['continuation'])
            remote = o.backend.data.get('requests', {}).get(g['id'], {})
            active = o.backend.current_turn()
            if (data['thread'] != g['brainThread'] or data['thread'] != o.backend.data.get('thread') or
                    active.get('id') != data['turn'] or active.get('status') != 'inProgress' or
                    remote.get('turn') != data['turn']):
                raise ValueError('不是此授權接續的原 Brain 執行回合。')
            if g['state'] == 'completed': return self.status(g['id'])
            if g['state'] != 'queued' or not g.get('authorization'): raise ValueError('接續沒有有效授權。')
        if not self.run_lock.acquire(blocking=False): return self.status(g['id'])
        try:
            for index in range(len(g['actions'])):
                with o.lock, o.db:
                    g = self.get(data['continuation'])
                    action = g['actions'][index]
                    if action['state'] == 'done': continue
                    if action['state'] == 'attempting':
                        raise ValueError('上次動作送達不確定；保留收據，不自動重送。')
                    self.execution_check(g['id'])
                    if action['scope']['kind'] != 'verify_delivery' and not self.execute:
                        raise ValueError('此安裝版沒有接續動作執行器。')
                    action.update(state='attempting', authorization=g['authorization']['id'], started=o.clock())
                    o._save('continuations', g)
                # Never hold the authority lock during network/native IO. A
                # newer stop/keep decision must fence the next write/action.
                if action['scope']['kind'] == 'verify_delivery':
                    result = {'verified': True, 'sources': [e['id'] for e in g['evidence']],
                              'meaning': '精確來源已核對；業務正確性仍由 Brain 依證據判斷。'}
                else:
                    result = self.execute(copy.deepcopy(g), copy.deepcopy(action), lambda: self.execution_check(g['id']))
                if result.get('verified') is not True: raise ValueError('動作尚無可核對完成證據。')
                with o.lock, o.db:
                    current = self.get(g['id'])
                    current['actions'][index].update(state='done', receipt=result, finished=o.clock())
                    o._save('continuations', current)
                    if current['state'] != 'queued': return self.status(g['id'])
            with o.lock, o.db:
                current = self.get(g['id'])
                if current['state'] == 'queued':
                    current.update(state='completed', error=''); o._save('continuations', current)
        except Exception as error:
            with o.lock, o.db:
                current = self.get(g['id'])
                if current['state'] != 'revoked':
                    current.update(state='blocked', error=str(error) if isinstance(error, (ValueError, OSError)) else '動作回應無法確認；收據已保存，沒有重送。')
                o._save('continuations', current)
        finally: self.run_lock.release()
        return self.status(g['id'])

    def notices(self):
        return {g.get('brainTurn'): {'notify': True, 'noticeID': g['id'], 'attentionIDs': [g['id']]}
                for g in self.o._rows('continuations') if g.get('brainTurn')}
