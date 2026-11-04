"""One durable read receipt for all representations; never calls a model/native IO."""

def outcome_id(job):
    return 'job-outcome:' + job['id']


class AttentionStore:
    def __init__(self, owner):
        self.owner = owner
        with owner.lock, owner.db:
            owner.db.execute('CREATE TABLE IF NOT EXISTS attention_reads(id TEXT PRIMARY KEY, at REAL NOT NULL)')

    def read(self, identities):
        o = self.owner
        with o.lock, o.db:
            o.db.executemany('INSERT OR IGNORE INTO attention_reads VALUES(?,?)',
                            [(identity, o.clock()) for identity in identities if isinstance(identity,str)])

    def is_read(self, identity):
        with self.owner.lock:
            return self.owner.db.execute('SELECT 1 FROM attention_reads WHERE id=?', (identity,)).fetchone() is not None

    def unread_policy(self, policy):
        return policy['notify'] and not all(self.is_read(i) for i in policy['attentionIDs'])

    def activity_id(self, activity):
        if activity['id'].startswith('failure:'):return 'activity:'+activity['id']
        job=next((j for j in self.owner._rows('jobs') if j['id']==activity.get('job')),None)
        return outcome_id(job) if job and job['state']=='blocked' else 'activity:'+activity['id']

    def policies(self):
        o = self.owner
        policies = o.reports.notices() if o.reports else {}
        jobs = {j['id']:j for j in o._rows('jobs')}
        requests = o.backend.data.get('requests', {}) if o.backend else {}
        for turn in o._rows('turns'):
            trigger = turn['trigger']
            if trigger['kind'] == 'user': continue
            identity = turn.get('brainTurn') or requests.get(turn['id'],{}).get('turn')
            if not identity: continue
            job = jobs.get(trigger.get('job'))
            allowed = bool(not turn.get('quiet') and job and job['state'] in {'completed','blocked'} and
                           not job.get('manualObservation') and trigger.get('outcome') == job['state'])
            # Old report-only observation replies and transport status replies
            # are history, not new deliverables. Closing has a concrete outcome.
            if trigger.get('closing'):
                allowed = trigger.get('outcome') in {'closed','failed','uncertain'}
            ids = [outcome_id(job)] if allowed and job else ['closing:'+trigger['closing']] if allowed else []
            policies[identity] = {'notify': bool(ids), 'noticeID':ids[0] if ids else '', 'attentionIDs':ids}
        return policies

    def activities(self):
        o = self.owner
        jobs = {j['id']:j for j in o._rows('jobs')}
        turns = {t['id']:t for t in o._rows('turns')}
        result = []
        for a in o._rows('activities')[-80:]:
            value = dict(a); job = jobs.get(a.get('job')); eligible = False
            if a['id'].startswith('failure:'):
                t = turns.get(a['id'][8:],{})
                eligible = t.get('state') == 'failed' and not t.get('quiet')
                value['resolved'] = a.get('resolved',False) or t.get('state') == 'done'
            elif job:
                if a['kind'] in {'waiting','uncertain'}: eligible = job['state'] == a['kind']
                elif a['kind'] in {'error','blocked'}: eligible = job['state'] in {'blocked','uncertain','opening'}
                value['resolved'] = a.get('resolved',False) or not eligible
            elif a['id'].startswith('health:'):
                eligible = not a.get('resolved',False)
            elif a['id'].startswith('event:'):
                t = turns.get(a['id'],{})
                closing=next((c for c in o._rows('closings') if c['id']==t.get('trigger',{}).get('closing')),None)
                eligible = bool(closing and closing['state'] in {'blocked','failed','uncertain'} and
                                t.get('trigger',{}).get('outcome')==closing['state'] and not a.get('resolved',False))
                value['resolved']=a.get('resolved',False) or bool(closing and closing['state']=='closed')
            # Unlinked legacy errors remain auditable without pretending they
            # are proven resolved or continuing to override current evidence.
            value.update(notificationEligible=eligible and not value.get('resolved',False), diagnosticOnly=not eligible)
            result.append(value)
        return result

    def health(self):
        o = self.owner
        if not o.observer: return
        gaps = [r for r in o.observer.records() if r.get('active') and
                r.get('coverage') not in {'current','catching_up','pending'}]
        with o.lock, o.db:
            episode = o._get('attentionHealth', {})
            if not gaps:
                if episode.get('id'):
                    row = next((a for a in o._rows('activities') if a['id']==episode['id']),None)
                    if row: row['resolved']=True; o._save('activities',row)
                    o._set('attentionHealth',{})
                return
            if not episode:
                count = o._get('attentionHealthSequence',0)+1
                o._set('attentionHealthSequence',count)
                episode={'id':'health:'+str(count), 'since':o.clock()}
                o._set('attentionHealth',episode)
            if o.clock()-episode['since'] >= 60:
                old=next((a for a in o._rows('activities') if a['id']==episode['id']),None)
                if not old:
                    o._activity('部分工作的來源持續無法確認，已保留上次資料並持續補查。',
                                kind='error', identity=episode['id'])

    def acknowledge(self, page, through, activities=()):
        policies=self.policies(); ids=[]
        for m in page['messages']:
            if m['cursor'] <= through and m.get('phase')=='final_answer':
                policy=policies.get(m.get('turn'))
                ids += policy['attentionIDs'] if policy is not None else ['brain-turn:'+m['turn']]
        ids += [self.activity_id(a) for a in self.owner._rows('activities') if a['id'] in activities]
        self.read(ids)

    def project(self, result, native, page):
        """Join exact native notice and job/source identity, preserving raw read state."""
        o=self.owner
        with o.lock, o.db:
            jobs=o._rows('jobs'); aliases=o._get('attentionAliases',{}); previous_aliases=dict(aliases)
            originals={s['id']:s for s in native.get('sessions',[])}
            rows=result['sessions']
            for row in rows:
                original=originals.get(row['id'])
                # Never attach a different binding/thread's receipt to this row.
                matches=lambda j:bool(original and j.get('session')==original['id'] and j.get('binding')==original.get('binding') and
                    (not j.get('generation') or j['generation']==original.get('bindingGeneration')) and
                    (not j.get('thread') or j['thread'] in {original.get('codexThreadID'),original.get('claudeSessionID')}))
                from brain_service import ACTIVE, human_wait
                if original and (row['id']==o.backend.session_id or any(matches(j) and j['state'] in ACTIVE and not j.get('manualObservation') for j in jobs)):
                    row['unread']=False
                job=next((j for j in jobs if matches(j) and j['state'] in {'completed','blocked'} and not j.get('manualObservation') and
                    j.get('sourceNotice') and j['sourceNotice']==original.get('noticeID')),None)
                if job:
                    key=outcome_id(job)
                    for identity in row.get('attentionIDs',[]): aliases[identity]=key
                    row['attentionIDs']=[key]
                    row.update(unread=False,notificationSuppressed=True)
                if original and any(matches(j) and j['state']=='waiting' and
                        j.get('sourceNotice')==original.get('noticeID') for j in jobs):
                    row['unread']=bool(original.get('unread'))
                if original and original.get('phase')=='waiting':
                    # An owned job must not hide its formal native approval.
                    row['unread']=bool(original.get('unread')) and bool(human_wait(original))
                    row.pop('notificationSuppressed',None)
                if original and row.get('noticeID') and not row.get('attentionIDs'):
                    row['attentionIDs']=['native:'+row['id']+':'+row['noticeID']]
                if row.get('notificationSuppressed'): row['unread']=False
                row['attentionIDs']=[aliases.get(i,i) for i in row.get('attentionIDs',[])]
            if aliases!=previous_aliases:o._set('attentionAliases',aliases)
            # Transfer historical read receipts when an exact late source links.
            for old,new in aliases.items():
                if self.is_read(old): self.read([new])
            for row in rows:
                original=originals.get(row['id'])
                if original and original.get('unread') is False:
                    self.read(row.get('attentionIDs',[]))
            # Desktop read of Brain and PWA read use the same attention IDs.
            original=originals.get(o.backend.session_id,{})
            if original.get('unread') is False and original.get('lastTurn'):
                matching=[m for m in page['messages'] if m.get('turn')==original['lastTurn']]
                if matching:self.acknowledge({'messages':matching},max(m['cursor'] for m in matching))
            allowed={i for row in rows for i in row.get('attentionIDs',[])}
            self.read(set(native.get('attentionRead',[])) & allowed)
            for row in rows:
                ids=row.get('attentionIDs',[])
                if ids and all(self.is_read(i) for i in ids):row['unread']=False
            return result

    def views(self, native, notifications):
        rows={r['id']:r for r in notifications}
        result=[]
        for source in native.get('sessions',[]):
            row=rows.get(source['id'],source)
            if source['id']==self.owner.backend.session_id:
                summary=rows.get('brain',{})
                row={**row, 'unread':bool(row.get('unread') or summary.get('unread')),
                     'attentionIDs':list(dict.fromkeys([*row.get('attentionIDs',[]),*summary.get('attentionIDs',[])]))}
            result.append({'id':source['id'],'binding':source.get('binding',''),
                'noticeID':source.get('noticeID',''),'unread':bool(row.get('unread')),
                'attentionIDs':row.get('attentionIDs',[])})
        return result
