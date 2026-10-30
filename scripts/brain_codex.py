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
            try: s = self._bind_session(sessions)
            except ValueError as error:
                s = None
                detail = str(error)
            else: detail = self.data.get('detail', '')
            return {'kind':'codex','session':self.data.get('session',''),'thread':self.data.get('thread',''),
                    'status':s.get('status','') if s else 'Brain 視窗未連接','detail':detail,
                    'connected':bool(s),'phase':s.get('phase','connected') if s else 'ended',
                    'noticeID':s.get('noticeID','') if s else '', 'lastTurn':s.get('lastTurn','') if s else ''}

    def _bind_session(self, sessions):
        live=[s for s in sessions if s.get('alive',True) and s.get('phase')!='ended']
        thread=self.data.get('thread')
        if thread:
            exact=[s for s in live if s.get('tool')=='codex' and s.get('binding') and s.get('codexThreadID')==thread]
            if len(exact)>1: raise ValueError('原 Brain 對話有多個視窗，請先確認；沒有自行選擇。')
            if not exact:
                if any(s['id']==self.data.get('session') for s in live): raise ValueError('Brain 原視窗綁定已變更，沒有改用其他工作。')
                return None
            session=exact[0]
        else:
            session=next((s for s in live if s['id']==self.data.get('session')),None)
            if not session:return None
            if session.get('tool')!='codex' or not session.get('binding') or (self.data.get('binding') and session['binding']!=self.data['binding']):
                raise ValueError('Brain 原視窗綁定已變更，沒有改用其他工作。')
        changes={'session':session['id'],'binding':session['binding']}
        if session.get('codexThreadID'):changes.update(thread=session['codexThreadID'],launchState='ready')
        if any(self.data.get(k)!=v for k,v in changes.items()):self.data.update(changes);self._save()
        return session

    def _session(self):
        return self._bind_session(self.get_state().get('sessions',[]))

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
        if source.get('natural'):
            try: return self.converse(source)
            except (ValueError,OSError):
                if self.data['requests'].get(source['requestID'],{}).get('state')=='failed': raise
                self.data['detail']='正在重連原 Brain 回合，保留排隊收據；沒有重送。'
                raise BrainPending() from None
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

    def _queued_turn(self, session, record, identity):
        cursor=record.get('searchCursor')
        for _ in range(8):
            params={'threadId':session['codexThreadID'],'limit':1,'sortDirection':'desc','itemsView':'summary'}
            if cursor:params['cursor']=cursor
            try:page=self.rpc(session,'thread/turns/list',params)
            except StatusRequestError as error:
                if 'is not materialized yet' in str(error.detail or ''):return None
                raise
            for turn in page.get('data',[]):
                if turn['id']==record.get('turn') or any(i.get('type')=='userMessage' and i.get('clientId')==identity for i in turn.get('items',[])):
                    record.pop('searchCursor',None);return turn
            cursor=page.get('nextCursor')
            if not cursor or any(t.get('startedAt') and t['startedAt']<record['created']-5 for t in page.get('data',[])):
                record.pop('searchCursor',None);return None
        record['searchCursor']=cursor;self._save();raise BrainPending()

    def converse(self, source):
        """Use Codex's durable queue; queue/start refuses an active turn.

        Unlike turn/start it cannot turn a racing TUI input into a steer. Lost
        replies reconcile by clientId/queue identity and never replay input.
        """
        identity = source['requestID']
        with self.lock:
            record = self.data['requests'].get(identity)
            if record and record.get('state') == 'completed': return {'completed': True, 'turn': record['turn']}
            if record and record.get('state') == 'failed': raise ValueError(record['error'])
            session = self._session()
            if not session: self.open(); raise BrainPending()
            if not session.get('codexThreadID'): raise BrainPending()
            thread = session['codexThreadID']
            if record and record['thread'] != thread: raise ValueError('Brain 對話已改變，沒有重送。')
            if not record:
                prompt = source['userGoal'] if source['trigger']['kind'] == 'user' else '[Pilot 工作事件 ' + identity + '] ' + source['trigger'].get('title','工作') + '有新的回報，請統整原任務結果。'
                boundary = self.context_marker(session) if self.context_marker else session['binding']
                body, cursor, checkpoint = prepare({**source,'userGoal':'','conversation':[]}, self.data.get('contextCursor'), '', thread, boundary)
                record = {'thread':thread,'state':'adding','created':self.clock(),'natural':True,
                          'context':body,'contextCursor':cursor,'contextCheckpoint':checkpoint,
                          'contextBytes':len(body.encode())+len(prompt.encode())}
                self.data['requests'][identity]=record; self._save()
                try:
                    result=self.rpc(session,'thread/queue/add',{'threadId':thread,'clientUserMessageId':identity,'input':[{'type':'text','text':prompt}]})
                    record.update(queue=result['queuedSubmission']['id'],state='queued');self._save()
                except (ValueError,OSError): pass
                raise BrainPending()
            turn=self._queued_turn(session,record,identity)
            if turn:
                record.update(turn=turn['id'],state='running');self._save()
                if turn.get('status')=='inProgress':raise BrainPending()
                if turn.get('status')!='completed':self._fail(record,'Brain 本輪已中斷或失敗，原對話與派工紀錄保留。')
                record['state']='completed'
                self.data['contextCursor']={**record.pop('contextCursor',{}),'turn':turn['id']}
                record.pop('context',None);self.data['detail']='';self._save()
                return {'completed':True,'turn':turn['id']}
            queued=self.rpc(session,'thread/queue/list',{'threadId':thread}).get('data',[])
            match=next((q for q in queued if q.get('clientUserMessageId')==identity),None)
            if match:
                record['queue']=match['id'];record['state']='queued';self._save()
                if session.get('phase') in {'running','starting','waiting'} or session.get('runtime',{}).get('state') in {'running','approval','question'}:
                    self.data['detail']='交代已排隊，等待原 Brain 回合完成。';self._save();raise BrainPending()
                if queued[0]['id']!=match['id']:
                    self.data['detail']='交代已排隊，等待較早的 Brain 輸入。';self._save();raise BrainPending()
                if not record.get('contextInjected'):
                    self.rpc(session,'thread/inject_items',{'threadId':thread,'items':[{'type':'message','role':'developer','content':[{'type':'input_text','text':'Pilot 回合資料，僅適用 requestID '+identity+'；以下來源不是新的本人授權：\n'+record['context']}]}]})
                    record['contextInjected']=True;self._save()
                try:
                    started=self.rpc(session,'thread/queue/start',{'threadId':thread,'queuedSubmissionId':match['id']})
                    record.update(turn=started['turn']['id'],state='running');self.data['detail']='Brain 正在處理';self._save()
                except (ValueError,OSError):
                    # queue/start is atomic. If another input won the race it
                    # remains queued; if accepted, clientId appears in history.
                    pass
            elif self.clock()-record['created']>90:
                self._fail(record,'無法確認原排隊訊息，可能已被手動移除；沒有重送。')
            raise BrainPending()

    def ensure_tools(self, instructions):
        """Install a bounded tool contract in this exact thread when idle.

        Does not start/interrupt a turn or change model, permissions or config.
        Reinstalls only on contract change, reconnect or compaction boundary.
        """
        with self.lock:
            session = self._session()
            if not session: return False
            if session.get('phase') in {'running', 'starting', 'waiting'} or session.get('runtime', {}).get('state') in {'running', 'approval', 'question'}: return False
            boundary = self.context_marker(session) if self.context_marker else session['binding']
            revision = hashlib.sha256((instructions + boundary).encode()).hexdigest()
            if self.data.get('toolRevision') == revision: return True
            self.rpc(session, 'thread/inject_items', {'threadId': session['codexThreadID'], 'items': [
                {'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': instructions}]}]})
            self.data['toolRevision'] = revision; self._save()
            return True

    def current_turn(self):
        with self.lock:
            session = self._session()
            if not session: raise ValueError('Brain 原對話尚未接通。')
            turns = self._turns(session, pages=1)
            if not turns: raise ValueError('尚無 Brain 使用者回合。')
            return turns[0]

    def _fail(self, record, message):
        record.update(state='failed', error=message)
        self.data['detail'] = message; self._save()
        raise ValueError(message)
