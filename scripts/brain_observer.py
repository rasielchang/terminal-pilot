"""Read-only work observations, independent of dispatch/job lifetime.

Only registered Pilot targets are read. Events and source cursors commit together;
this module has no launch/send/approval/close capability. A projection is evidence,
never authority to execute instructions quoted in a worker conversation.
"""
import hashlib
import json
from brain_attention import source_kind, acknowledgment, answers_wait


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def observer_id(target):
    return 'work:' + digest([target['tool'], target.get('agentHome', ''),
                              target['directory'], target['thread']])[:40]


class WorkObserver:
    def __init__(self, owner, read_page, normalize=None):
        self.owner, self.read_page = owner, read_page
        self.normalize = normalize or (lambda target: target)
        self.db, self.lock, self.clock = owner.db, owner.lock, owner.clock
        with self.lock, self.db:
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS work_observers(id TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS work_events(
                revision INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                observer TEXT NOT NULL, data TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS work_events_observer ON work_events(observer,revision);
            ''')

    def records(self):
        return self.owner._rows('work_observers')

    def save(self, record):
        self.owner._save('work_observers', record)

    def event(self, record, identity, kind, text='', *, source=None, notify=True):
        value = {'id': identity, 'work': record['id'], 'kind': kind,
                 'text': text[:2000], 'partial': len(text) > 2000,
                 'observedAt': self.clock(), 'source': source or {}, 'reportOnly': True,
                 'notify': notify, 'topics': record.get('topics', [])}
        result = self.db.execute('INSERT OR IGNORE INTO work_events(id,observer,data) VALUES(?,?,?)',
                                 (identity, record['id'], json.dumps(value, ensure_ascii=False)))
        if result.rowcount:
            record['revision'] = result.lastrowid
        return bool(result.rowcount)

    def discover(self, state):
        jobs, topics = self.owner._rows('jobs'), {t['id']: t for t in self.owner._rows('topics')}
        known = {r['id']: r for r in self.records()}
        catalog = self.owner._catalog(state)
        targets = list(catalog.values()) + (self.owner.target_source() if self.owner.target_source else [])
        for target in targets:
            if target['mode'] != 'specific': continue
            if self.owner.backend and target['tool']=='codex' and target['thread']==self.owner.backend.data.get('thread'): continue
            target = self.normalize(target)
            identity = observer_id(target)
            linked = [j for j in jobs if j.get('target') and observer_id(self.normalize(j['target'])) == identity]
            topic_ids = sorted({j['topic'] for j in linked if j.get('topic')})
            # An explicit task closure, not a job result/window close, retires observation.
            archived = bool(topic_ids) and all(topics.get(t, {}).get('state') == 'done' for t in topic_ids)
            if identity not in known:
                if archived: continue
                known[identity] = {'id': identity, 'target': target, 'topics': topic_ids,
                    'registeredAt': self.clock(), 'cursor': 0, 'sourceRevision': '',
                    'coverage': 'pending', 'lastVerifiedAt': None, 'revision': 0,
                    'status': 'unknown', 'waiting': None, 'active': True}
                self.event(known[identity], 'registered:'+identity, 'registered', notify=False)
            record = known[identity]
            if record.get('topics') != topic_ids or record.get('active') != (not archived):
                self.event(record, 'scope:'+digest([identity, topic_ids, archived, record.get('revision')]),
                           'scope', notify=False)
            record.update(topics=topic_ids, active=not archived)
            # Preserve the exact home/thread. Display labels are not identities.
            record['target'] = target
            self.save(record)
        return known

    def reconcile(self, state=None, *, limit=32):
        """Bounded rotating sweep. No source failure can erase last confirmed data."""
        try: state = self.owner.get_state() if state is None else state
        except (OSError,ValueError): state = {'sessions':[], 'snapshotReliable':False}
        with self.lock, self.db:
            try: records = self.discover(state)
            except (OSError,ValueError):
                records = {r['id']:r for r in self.records()}
                state = {**state,'snapshotReliable':False}
            ordered = sorted((r for r in records.values() if r['active']),
                             key=lambda r: (r.get('lastAttemptAt', 0), r['id']))
            for record in ordered[:limit]:
                self._read(record, state)
        return self.snapshot()

    def _read(self, record, state):
        projection = lambda: {k:v for k,v in self.project(record).items() if k!='lastVerifiedAt'}
        before = digest(projection())
        previous_coverage = record.get('coverage')
        record['lastAttemptAt'] = self.clock()
        target = record['target']
        from brain_targets import conversation_target
        matches = [s for s in state.get('sessions', [])
                   if (t := conversation_target(s)) and observer_id(self.normalize(t)) == record['id']]
        if len(matches) > 1:
            self._gap(record, 'ambiguous_binding'); return
        current = matches[0] if matches else None
        try:
            page = self.read_page(target, after=record['cursor'], reader=record.get('reader'))
            self.owner._set('observationScannedBytes',self.owner._get('observationScannedBytes',0)+page.get('scannedBytes',0))
            if page.get('thread') != target['thread']: raise ValueError('wrong_thread')
            revision = page['revision']
            if record['sourceRevision'] and record['sourceRevision'] != revision:
                # Re-index exact source, dedup by source item rather than arrival order.
                record['cursor'] = 0
                record['resetPending'] = True
                self.event(record, 'gap:'+digest([record['id'], record['sourceRevision'], revision]),
                           'source_reset', '原來源版本已改變，正在重新核對。')
                # A physical scanner reset already returns page one and its
                # persisted epoch. Do not discard it and create another epoch.
                if not page.get('reset'): page = self.read_page(target, after=0, reader=None)
                if page.get('thread') != target['thread'] or page['revision'] != revision:
                    raise ValueError('source_changed_during_read')
                record['bootstrapThrough'] = page['latestCursor']
            record['sourceRevision'] = revision
            record['reader'] = page.get('reader')
            record.setdefault('bootstrapThrough', page['latestCursor'])
            for item in page['messages']:
                if item['cursor'] <= record['cursor']: continue
                source = {k: item.get(k) for k in ('id', 'cursor', 'turn', 'thread', 'created')}
                source['revision'] = revision
                text = item.get('text', '')
                role = item.get('role')
                kind = source_kind(item)
                if role == 'user' and any(j.get('wire') == text for j in self.owner._rows('jobs')): kind = 'dispatch_receipt'
                notify = kind in {'instruction', 'result', 'error'} and (
                    item['cursor'] > record['bootstrapThrough'] or
                    item.get('created', 0) > record['registeredAt'] or
                    record.get('resetPending') and not item.get('created'))
                notify = bool(notify) and not (kind=='instruction' and acknowledgment(text))
                identity = 'source:'+digest([record['id'], item['id'], item.get('turn'), role, text])
                added = self.event(record, identity, kind, text, source=source, notify=notify)
                if added and kind=='instruction' and record.get('semanticWaiting') and answers_wait(text,record['semanticWaiting']):
                    wait=record['semanticWaiting']
                    if not wait.get('answeredBy') and record['revision']>wait['revision']:
                        wait['answeredBy']=identity; wait['answerState']='awaiting_confirmation'
                # Source offset orders this stream; duplicates never overwrite later summaries.
                if added or not record.get('latest'):
                    record['latest'] = {'event': identity, 'kind': kind, 'text': text[:2000], 'source': source}
                    if kind in {'instruction', 'result', 'error'}:
                        record[kind] = record['latest']
                record['cursor'] = item['cursor']
            record['coverage'] = 'catching_up' if page.get('hasMore') else 'current'
            if record['coverage'] == 'current' and previous_coverage not in {'pending','current','catching_up'}:
                self.event(record, 'recovered:'+digest([record['id'],record.get('revision'),revision]),
                           'source_recovered', '原來源已恢復補查。')
            record['lastVerifiedAt'] = self.clock()
            record.pop('error', None)
            if not page.get('hasMore'): record.pop('resetPending', None)
            if state.get('snapshotReliable') is False:
                self._gap(record, 'runtime_unavailable')
            elif current: self._runtime(record, current)
            elif record.get('binding'):
                record['windowState'] = 'not_listed'
                # Absence from a list never resolves a natural-language blocker.
            if digest(projection()) != before:
                self.event(record,'projection:'+digest([record['id'],record.get('revision'),projection()]),'projection',notify=False)
            self.save(record)
        except (OSError, ValueError, KeyError, TypeError):
            self._gap(record, 'source_unavailable')
            if current and state.get('snapshotReliable') is not False:
                self._runtime(record, current)
                self.save(record)

    def _gap(self, record, reason):
        if record.get('coverage') != reason:
            self.event(record, 'gap:'+digest([record['id'], reason, record.get('revision', 0)]),
                       'source_gap', '來源尚未確認；保留上次結果與待本人狀態。')
        record.update(coverage=reason, error=reason)
        self.save(record)

    def _runtime(self, record, session):
        from brain_service import human_wait, running
        stamp = session.get('updatedAt', 0)
        generation = session.get('bindingGeneration') or session.get('binding', '')
        sequence = session.get('eventSequence', 0)
        old = record.get('runtime', {})
        if old.get('generation') == generation and (sequence < old.get('sequence', 0) or stamp < old.get('stamp', 0)):
            return  # Delayed state cannot resurrect a resolved wait.
        phase = ('error' if session.get('phase') == 'error' else 'waiting' if human_wait(session) else
                 'running' if running(session) or session.get('phase') == 'waiting' else
                 'ended' if session.get('phase') == 'ended' or session.get('alive') is False else session.get('phase', 'unknown'))
        approvals = sorted(str(a.get('id')) for a in session.get('runtime', {}).get('approvals', []))
        key = digest([phase, approvals, session.get('noticeID') if phase in {'waiting','error','complete'} else '',
                      session.get('activeTurn'), session.get('lastTurn'), generation])
        record.update(binding=session.get('binding'), bindingGeneration=generation,
                      session=session['id'], windowState='present')
        if key != old.get('key'):
            identity = 'runtime:'+digest([record['id'], key, sequence, stamp])
            self.event(record, identity, phase, session.get('status', phase),
                       source={'thread': record['target']['thread'], 'generation': generation,
                               'sequence': sequence, 'updatedAt': stamp, 'approvals': approvals},
                       notify=phase in {'waiting','error'} or record.get('waiting') is not None)
            record['status'] = phase
            if phase == 'waiting':
                record['waiting'] = {'id': identity, 'kind': 'runtime', 'text': session.get('status', '需要本人決定'),
                                     'source': identity, 'approvals': approvals}
            elif phase in {'running','complete'} and (record.get('waiting') or {}).get('kind') == 'runtime':
                record['waiting'] = None
        record['runtime'] = {'key': key, 'generation': generation, 'stamp': stamp, 'sequence': sequence,
                             'event': identity if key != old.get('key') else old.get('event')}

    def snapshot(self, *, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('工作摘要游標或頁面大小無效。')
        with self.lock:
            records = self.records()
            changed = sorted((r for r in records if r['revision'] > after), key=lambda r: (r['revision'], r['id']))
            rows = changed[:limit]
            return {'reportOnly': True, 'revision': max((r['revision'] for r in records), default=0),
                    'cursor': rows[-1]['revision'] if rows else after, 'hasMore': len(changed) > limit,
                    'asOf':self.clock(), 'works': [self.project(r) for r in rows], 'coverage': {'total': sum(r['active'] for r in records),
                        'current': sum(r['active'] and r['coverage'] == 'current' and self.fresh(r) for r in records),
                        'gaps': [{'work': r['id'], 'reason': r['coverage'] if r['coverage']!='current' else 'stale', 'lastVerifiedAt': r['lastVerifiedAt']}
                                 for r in records if r['active'] and (r['coverage'] != 'current' or not self.fresh(r))]}}

    def fresh(self, record):
        return record.get('lastVerifiedAt') is not None and self.clock()-record['lastVerifiedAt']<=60

    def project(self, record):
        # Reader state and raw filesystem paths never enter model context.
        fields=('id','topics','revision','status','active','coverage','lastVerifiedAt','windowState',
                'session','bindingGeneration','summary','instruction','result','latest')
        value={k:record[k] for k in fields if k in record}
        value['title']=record['target'].get('title','工作')
        value['waiting']=[w for w in (record.get('waiting'),record.get('semanticWaiting')) if w and not w.get('answeredBy')]
        if (record.get('semanticWaiting') or {}).get('answeredBy'):
            value['respondedWaiting']=record['semanticWaiting']
        return value
