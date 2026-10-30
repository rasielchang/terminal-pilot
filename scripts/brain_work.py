"""Durable open/resume intents sharing Brain's SQLite transaction and send fence."""
import hashlib
import uuid

from brain_targets import conversation_target
from pilot_http import UnsupportedOperation


class BrainWork:
    def _catalog(self, state):
        with self.lock, self.db:
            known = {t['id']: t for t in self._rows('targets')}
            incoming = self.target_source() if self.target_source else []
            incoming += [t for s in state.get('sessions', []) if (t := conversation_target(s))
                         and (not self.backend or s['id'] != self.backend.session_id)]
            brain_thread = getattr(self.backend, 'data', {}).get('thread') if self.backend else None
            for target in incoming:
                if target['thread'] == brain_thread: continue
                old = known.get(target['id'], {})
                if target.get('updated', 0) >= old.get('updated', 0):
                    known[target['id']] = {**old, **target}
                    if known[target['id']] != old: self._save('targets', known[target['id']])
            for project in state.get('projects', []):
                if project.get('tool') not in {'codex', 'claude'}: continue
                identity = 'project:' + project['id']
                known[identity] = {'id': identity, 'projectID': project['id'], 'directory': project['directory'],
                                   'tool': project['tool'], 'title': project['name'], 'mode': 'new', 'thread': ''}
            return known

    @staticmethod
    def _matching(target, sessions):
        if target['mode'] != 'specific': return []
        return [s for s in sessions if s.get('alive', True) and s.get('phase') != 'ended'
                and conversation_target(s) and conversation_target(s)['id'] == target['id']]

    def _opening(self, target, *, title='', job=''):
        return {'id': str(uuid.uuid4()), 'target': target, 'title': title or target['title'], 'job': job,
                'state': 'queued', 'session': '', 'binding': '', 'created': self.clock(), 'detail': '等待開啟原工作'}

    def open_work(self, data):
        if not self.launch_work: raise UnsupportedOperation('此服務尚未支援開啟工程 Terminal。')
        if set(data) != {'requestID', 'job'} or not isinstance(data['job'], str): raise ValueError('工作來源不正確。')
        with self.lock, self.db:
            fingerprint, old = self._receipt(data)
            if old: return old
            job = next((j for j in self._rows('jobs') if j['id'] == data['job']), None)
            if not job: raise ValueError('找不到原交辦。')
            target = job.get('target')
            if not target:
                matches = [t for t in self._catalog(self.get_state()).values() if t['thread'] == job.get('thread') and t['mode'] == 'specific']
                target = matches[0] if len(matches) == 1 else None
            if not target or target.get('mode') != 'specific': raise ValueError('原對話識別尚未保存，沒有另開替代對話。')
            existing = next((o for o in self._rows('openings') if o['target']['id'] == target['id']
                             and o['state'] in {'queued', 'starting', 'uncertain'}), None)
            opening = existing or self._opening(target)
            opening['topic'] = job.get('topic', '')
            if not existing: self._save('openings', opening)
            result = self._record(data, fingerprint, {'opening': opening['id'], 'status': opening['state'], 'session': opening['session']})
        self.wake.set()
        return result

    def _stage_launches(self, plan, turn, source, fresh, jobs, topic_ids):
        actions = plan.get('launches', [])
        if not actions: return [], []
        if not self.launch_work: raise ValueError('此服務不支援開啟工程 Terminal。')
        catalog = self._catalog({'sessions': list(fresh.values()), 'projects': self.get_state().get('projects', [])})
        allowed = {t['id']: t for t in source.get('targets', [])}
        staged, openings = [], []
        for action in actions:
            target = catalog.get(action['target'])
            if not target or target['id'] not in allowed or any(target.get(k) != allowed[target['id']].get(k) for k in ('tool', 'directory', 'mode')): raise ValueError('開啟目標已改變，沒有開啟或改送其他工作。')
            if any((j.get('target') or {}).get('id') == target['id'] and j['state'] not in {'completed', 'blocked', 'manual'} for j in jobs):
                raise ValueError('原對話已有未完成交辦，沒有重複啟動。')
            if any(o['target']['id'] == target['id'] and o['state'] in {'queued', 'starting', 'uncertain'} for o in self._rows('openings')):
                raise ValueError('正在確認原工作開啟結果，沒有重複開啟。')
            matches = self._matching(target, list(fresh.values()))
            if len(matches) > 1: raise ValueError('同一對話開在多個視窗，請先確認要使用哪個工作。')
            if matches and (any(j['session'] == matches[0]['id'] and j['state'] not in {'completed', 'blocked', 'manual'} for j in jobs)
                            or any(a['session'] == matches[0]['id'] for a in plan['dispatches'])):
                raise ValueError('同一工作不能重複派送或同時開啟。')
            if matches and self._get('manual:' + matches[0]['id'], 0) and self._get('manual:' + matches[0]['id']) >= turn['userCreated']:
                raise ValueError('你已手動接手，沒有重新派工。')
            if any(j['state'] == 'manual' and j.get('finished', 0) >= turn['userCreated'] and (j.get('target') or {}).get('id') == target['id'] for j in jobs) and turn['trigger']['kind'] != 'user':
                raise ValueError('原對話已交回手動，沒有自動續接。')
            if any(j['goal'] == turn['goal'] and (j.get('target') or {}).get('id') == target['id']
                   and j['instruction'] == action['instruction'] for j in jobs):
                raise ValueError('相同交辦已處理，沒有重新開啟或重送。')
            baseline = None
            if action['instruction'].strip():
                if matches:
                    baseline = turn['baselines'].get(matches[0]['id'])
                    if baseline is None: raise ValueError('原工作在規劃後才開啟，沒有覆蓋其中的新交代。')
                elif target['mode'] == 'specific' and self.describe_target:
                    baseline = self.describe_target(target).get('baseline')
                    if baseline is None: raise ValueError('無法讀取原對話的交代，沒有另開或改送替代對話。')
            identity = str(uuid.uuid4()) if action['instruction'].strip() else ''
            opening = self._opening(target, title=action['title'], job=identity)
            opening['topic'] = topic_ids.get(action['topic'], action['topic'])
            openings.append(opening)
            if identity:
                topic = action['topic']
                staged.append({'id': identity, 'goal': turn['goal'], 'topic': topic_ids.get(topic, topic),
                    'session': opening['id'], 'binding': '', 'thread': target['thread'], 'target': target,
                    'title': action['title'], 'instruction': action['instruction'],
                    'wire': self._wire(identity, action['instruction']), 'state': 'opening', 'created': self.clock(),
                    'detail': '正在開啟／精確續接原工作', 'baseline': '', 'resumeBaseline': baseline})
        return staged, openings

    @staticmethod
    def _wire(identity, instruction):
        return f"[Brain 交辦 {identity}]\n{instruction.strip()}\n\n請在這個對話完成交辦並回報實際結果、驗證及阻礙；沿用既有核准規則。"

    def _advance_openings(self, state):
        if not self.launch_work: return
        with self.lock:
            pending = [o for o in self._rows('openings') if o['state'] in {'queued', 'starting', 'uncertain'}]
        for opening in pending:
            with self.lock, self.db:
                if opening['state'] == 'queued' and (self._get('paused', False) or any(
                    t['state'] in {'queued', 'planning', 'codex_wait'} and t['trigger']['kind'] == 'user' for t in self._rows('turns'))): continue
                job = next((j for j in self._rows('jobs') if j['id'] == opening['job']), None)
                if job and job['state'] != 'opening' and opening['state'] == 'queued':
                    opening.update(state='failed', detail='交辦已撤回，沒有開啟。'); self._save('openings', opening); continue
                target = opening['target']
                if opening['state'] == 'starting' and self.clock() - opening['created'] > 90:
                    opening.update(state='uncertain', detail='原工作尚未接通，可能等待初始化或資料夾信任；沒有重複啟動。')
                    self._save('openings', opening)
                    if job: self._report(opening['detail'], topic=job.get('topic', ''), kind='error')
                matches = self._matching(target, state.get('sessions', [])) if opening['state'] == 'queued' else [
                    s for s in state.get('sessions', []) if s['id'] == (opening['session'] or opening['id'])]
                if len(matches) > 1:
                    self._opening_failed(opening, job, '同一對話有多個視窗，沒有自行選擇。'); continue
                if matches:
                    s = matches[0]; actual = conversation_target(s)
                    if not actual or not s.get('binding'):
                        if s.get('phase') == 'waiting':
                            opening['detail'] = '原工作等待確認，請處理原始確認卡片。'; self._save('openings', opening)
                        continue
                    if (actual['tool'] != target['tool'] or actual['directory'] != target['directory']
                            or (target['thread'] and actual['thread'] != target['thread'])
                            or not s.get('alive', True) or s.get('phase') in {'ended', 'error'}
                            or (opening['binding'] and s['binding'] != opening['binding'])):
                        self._opening_failed(opening, job, '開啟後的對話與原目標不符，沒有送出。'); continue
                    try:
                        thread = self.get_thread(s['id'])
                        if thread['session'].get('binding') != s['binding'] or not conversation_target(thread['session']) or conversation_target(thread['session'])['id'] != actual['id']: continue
                    except (ValueError, OSError): continue
                    opening.update(state='ready', session=s['id'], binding=s['binding'], detail='已接回原對話')
                    self._save('openings', opening)
                    if job and job['state'] == 'opening':
                        if self._get('manual:' + s['id'], 0) >= job['created']:
                            self._finish(job, 'manual', '你已在原對話操作，沒有插入新交辦。'); continue
                        user = next((m.get('text', '') for m in reversed(thread['messages']) if m.get('role') == 'user'), '')
                        if job.get('resumeBaseline') is not None and job['resumeBaseline'] != hashlib.sha256(user.encode()).hexdigest():
                            self._finish(job, 'manual', '原對話已有新的使用者交代，沒有插入或重送。'); continue
                        job.update(session=s['id'], binding=s['binding'], thread=actual['thread'], target=actual,
                                   baseline=hashlib.sha256(user.encode()).hexdigest(), state='queued', detail='等待工作空下來')
                        self._save('jobs', job)
                    continue
                if opening['state'] == 'queued':
                    opening.update(state='starting', detail='正在連接工程對話'); self._save('openings', opening)
                    self.db.commit()  # Crash/lost response must never replay a native launch.
                    try:
                        result = self.launch_work({'launchID': opening['id'], 'tool': target['tool'], 'directory': target['directory'],
                            'name': opening['title'], 'mode': target['mode'], 'sessionID': target['thread']})
                        if result.get('opened') and result.get('session') and (result['session'] == opening['id'] or target['mode'] == 'specific'):
                            opening.update(session=result['session'], binding=result.get('binding', ''))
                        else: opening.update(state='uncertain', detail='無法確認開啟結果；沒有重複啟動。')
                    except (ValueError, OSError): opening.update(state='uncertain', detail='開啟結果尚未確認；保留原請求，不重複啟動。')
                    self._save('openings', opening)
                    if opening['state'] == 'uncertain' and job:
                        self._report(opening['detail'], topic=job.get('topic', ''), kind='error')

    def _opening_failed(self, opening, job, detail):
        opening.update(state='failed', detail=detail); self._save('openings', opening)
        if job and job['state'] == 'opening': self._finish(job, 'blocked', detail)
