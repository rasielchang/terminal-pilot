"""Persistent Brain TUI/thread adapter. No separate model, key or approval policy.

The journal is committed before launch/start. Ambiguous writes are observed,
never replayed. A tick does bounded RPC reads; approvals and other work continue.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from brain_context import prepare
from brain_provider import INSTRUCTIONS, plan_schema, validate_plan
from codex_live import StatusRequestError
from web_push import private_write


class BrainPending(Exception):
    pass


class CodexBrain:
    def __init__(self, store, state, native, rpc, *, clock=time.time, context_marker=None):
        self.path = Path(store) / 'brain-codex.json'
        self.get_state, self.native, self.rpc, self.clock = state, native, rpc, clock
        self.context_marker = context_marker
        self.lock = threading.RLock()
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'requests': {}}
        if not isinstance(self.data, dict) or not isinstance(self.data.get('requests'), dict):
            raise ValueError('Brain Codex 紀錄無法讀取；未建立替代對話。')

    def _save(self):
        private_write(self.path, json.dumps(self.data, ensure_ascii=False).encode())
        with self.path.open('r+b') as stream: os.fsync(stream.fileno())

    @property
    def session_id(self):
        with self.lock: return self.data.get('session', '')

    def snapshot(self, sessions):
        with self.lock:
            s = next((s for s in sessions if s['id'] == self.data.get('session')), None)
            if (s and s.get('alive', True) and s.get('tool') == 'codex' and s.get('binding')
                    and s.get('binding') == self.data.get('binding') and s.get('codexThreadID')
                    and not self.data.get('thread')):
                self.data.update(thread=s['codexThreadID'], launchState='ready', detail=''); self._save()
            status = s.get('status', '') if s else ('尚未開啟' if not self.data.get('session') else 'Brain 視窗未連接')
            return {'kind': 'codex', 'session': self.data.get('session', ''),
                    'thread': self.data.get('thread', ''), 'status': status,
                    'detail': self.data.get('detail', ''), 'connected': bool(s and s.get('alive', True))}

    def _session(self):
        s = next((s for s in self.get_state().get('sessions', []) if s['id'] == self.data.get('session')), None)
        if not s or not s.get('alive', True) or s.get('phase') == 'ended': return None
        if s.get('tool') != 'codex' or not s.get('binding'):
            raise ValueError('Brain 原視窗綁定已變更，沒有改用其他工作。')
        if self.data.get('binding') and self.data['binding'] != s['binding']:
            raise ValueError('Brain 原視窗綁定已變更，沒有改用其他工作。')
        thread = s.get('codexThreadID')
        if thread and self.data.get('thread') and thread != self.data['thread']:
            raise ValueError('Brain 的 Codex 對話已變更，沒有送出新交代。')
        if thread and (self.data.get('thread') != thread or self.data.get('launchState') != 'ready'):
            self.data.update(thread=thread, binding=s['binding'], launchState='ready', detail='')
            self._save()
        return s

    def open(self):
        with self.lock:
            current = self._session()
            if current: return {'opened': True, 'session': current['id'], 'binding': current['binding']}
            if self.data.get('launchState') == 'starting':
                if self.clock() - self.data.get('openedAt', 0) < 45: raise BrainPending()
                raise ValueError('Brain 開啟結果尚未確認，請查看電腦上的 Brain 視窗；沒有重複開啟。')
            if self.data.get('session') and not self.data.get('thread'):
                raise ValueError('尚未記錄到 Brain 對話，請先查看原視窗；沒有建立另一段對話。')
            identity = str(uuid.uuid4())
            self.data.update(session=identity, binding='', launchState='starting', openedAt=self.clock(), detail='正在開啟 Brain Codex…')
            self._save()
            result = self.native('brainOpen', {'launchID': identity, 'threadID': self.data.get('thread', '')})
            if not result.get('opened') or result.get('session') != identity:
                raise ValueError('Brain 開啟結果尚未確認，沒有重複開啟。')
            self.data['binding'] = result['binding']; self._save()
            return result

    def _turns(self, session, wanted=None, *, pages=20):
        # Codex 0.163 exposes turn summaries with user/final messages; its
        # advertised thread/items/list method is not implemented. One turn per
        # page keeps tool logs and accumulated history out of proxy frames.
        result, cursor = [], None
        for _ in range(pages):
            args = {'threadId': session['codexThreadID'], 'limit': 1,
                    'sortDirection': 'desc', 'itemsView': 'summary'}
            if cursor: args['cursor'] = cursor
            page = self.rpc(session, 'thread/turns/list', args)
            result.extend(page.get('data', []))
            cursor = page.get('nextCursor')
            if not cursor or (wanted and any(t['id'] == wanted for t in result)): break
        return result

    def __call__(self, source):
        identity = source['requestID']
        with self.lock:
            record = self.data['requests'].get(identity)
            if record and record.get('state') == 'completed': return validate_plan(record['plan'])
            if record and record.get('state') == 'failed': raise ValueError(record['error'])
            session = self._session()
            if not session:
                self.open(); raise BrainPending()
            if not session.get('codexThreadID') or session.get('runtime', {}).get('state') == 'unavailable':
                if self.clock() - self.data.get('openedAt', 0) > 90:
                    raise ValueError('Brain Codex 尚未接通，請查看 Brain 視窗中的初始化或確認提示。')
                raise BrainPending()
            thread = session['codexThreadID']
            if record and record['thread'] != thread:
                raise ValueError('Brain 回合所屬對話已變更，沒有重送交代。')
            if not record:
                if session.get('phase') in {'running', 'starting', 'waiting'} or session.get('runtime', {}).get('state') in {'running', 'approval', 'question'}:
                    raise BrainPending()
                recent = []
                try: recent = self._turns(session, pages=1)
                except StatusRequestError as error:
                    # A new real TUI has a loaded thread but no rollout until
                    # the first user message. This is readiness, not failure.
                    if 'is not materialized yet' not in str(error.detail or ''):
                        raise BrainPending() from None
                except (ValueError, OSError): raise BrainPending() from None
                marker = '[Pilot Brain 回合 ' + identity + ']'
                try: boundary = self.context_marker(session) if self.context_marker else session.get('binding', '')
                except (ValueError, OSError): raise BrainPending() from None
                if any(i.get('type') == 'contextCompaction' for t in recent for i in t.get('items', [])):
                    boundary = recent[0]['id']
                previous = self.data.get('contextCursor')
                # An external/manual turn means the previous checkpoint may no longer
                # describe the live conversation. Restore our scoped checkpoint.
                if recent and previous and recent[0]['id'] != previous.get('turn'):
                    previous = None
                body, cursor, checkpoint = prepare(source, previous, getattr(self, 'instructions', INSTRUCTIONS), thread, boundary)
                prompt = marker + '\n' + body
                record = {'thread': thread, 'marker': marker, 'state': 'starting', 'created': self.clock(),
                          'fingerprint': hashlib.sha256(prompt.encode()).hexdigest(),
                          'contextCursor': cursor, 'contextCheckpoint': checkpoint, 'contextBytes': len(prompt.encode())}
                self.data['requests'][identity] = record
                self.data['detail'] = 'Brain Codex 正在處理你的交代…'; self._save()
                try:
                    response = self.rpc(session, 'turn/start', {'threadId': thread,
                        'input': [{'type': 'text', 'text': prompt}], 'clientUserMessageId': identity,
                        'outputSchema': plan_schema(source)})
                    turn = response.get('turn', {}).get('id')
                    if turn: record.update(turn=turn, state='running'); self._save()
                except (ValueError, OSError):
                    self.data['detail'] = '正在確認原回合是否已收到；不重複送出。'; self._save()
                raise BrainPending()
            try: turns = self._turns(session, record.get('turn'))
            except (ValueError, OSError):
                if self.data.get('detail') != '正在重新連接原 Brain 回合；沒有重送交代。':
                    self.data['detail'] = '正在重新連接原 Brain 回合；沒有重送交代。'; self._save()
                raise BrainPending() from None
            turn = next((t for t in turns if t['id'] == record.get('turn')), None)
            if not record.get('turn'):
                # Lost turn/start reply: correlate the exact journal marker, never latest/cwd.
                for candidate in turns:
                    if candidate.get('startedAt') and candidate['startedAt'] < record['created'] - 5: continue
                    items = candidate.get('items', [])
                    if any(item.get('type') == 'userMessage' and any(
                            p.get('type') == 'text' and p.get('text', '').startswith(record['marker'] + '\n')
                            for p in item.get('content', [])) for item in items):
                        record.update(turn=candidate['id'], state='running'); self._save(); turn = candidate; break
                if not turn and self.clock() - record['created'] > 45:
                    self._fail(record, '無法確認 Brain 是否收到這次交代；請查看 Brain 對話，這次沒有重送。')
            if not turn and record.get('turn') and self.clock() - record['created'] > 90:
                self._fail(record, '找不到原 Brain 回合的完成紀錄；請查看原始對話，沒有重送交代。')
            if not turn or turn.get('status') == 'inProgress': raise BrainPending()
            if turn.get('status') != 'completed':
                self._fail(record, 'Brain Codex 這一回合已中斷或失敗，請查看原始對話；沒有新增交辦。')
            items = list(reversed(turn.get('items', [])))
            final = next((item.get('text', '') for item in items if item.get('type') == 'agentMessage'
                          and item.get('phase') == 'final_answer'), '')
            try: plan = validate_plan(json.loads(final))
            except (ValueError, TypeError): self._fail(record, 'Brain Codex 回覆無法作為交辦格式，請查看原始對話；沒有新增交辦。')
            record.update(state='completed', plan=plan)
            if record.get('contextCursor'):
                self.data['contextCursor'] = {**record.pop('contextCursor'), 'turn': turn['id']}
                self.data['contextBoundary'] = self.data['contextCursor']['boundary']
            self.data['detail'] = ''; self._save()
            return plan

    def _fail(self, record, message):
        record.update(state='failed', error=message)
        self.data['detail'] = message; self._save()
        raise ValueError(message)
