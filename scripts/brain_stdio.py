"""Owned app-server subprocess; uses Pilot's existing approval protocol and journal."""
from collections import OrderedDict, deque
import os
from pathlib import Path
import subprocess
import threading
import uuid

from codex_approvals import ApprovalProxy
from codex_live import StatusProxy
from brain_codex import CodexBrain
from brain_provider import INSTRUCTIONS
from brain_permissions import load_permissions, thread_permissions, turn_permissions


class StdioProxy(ApprovalProxy):
    methods = ApprovalProxy.methods | {'thread/start', 'account/read'}

    def __init__(self, binary, home, cwd):
        self.approval_lock = threading.RLock()
        self.requests, self.items, self.receipts = OrderedDict(), OrderedDict(), OrderedDict()
        self.subscribed, self.lifecycle = set(), deque(maxlen=256)
        self.lock, self.pending, self.closed = threading.RLock(), {}, False
        environment = {**os.environ, 'CODEX_HOME': str(home)}
        self.process = subprocess.Popen(
            [str(binary), 'app-server', '--listen', 'stdio://'], cwd=cwd, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'terminal_pilot', 'version': '0.13.0'},
                                    'capabilities': {'experimentalApi': True}})
            self._write({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise


class BackgroundBrain(CodexBrain):
    def __init__(self, store, proxy, root, name):
        self.proxy, self.root = proxy, str(Path(root).resolve())
        self.current = None
        self.permissions = load_permissions(store)
        super().__init__(store, self.state, self.launch, self.rpc_call)
        self.instructions = INSTRUCTIONS.replace('~/brain', self.root)
        self.instructions = self.instructions.replace(
            '沒有已開啟的合適 agent 時告知需在「工作管理」開啟／續接，不編造 session 或聲稱已啟動。',
            '本服務只有你這個助理，直接使用自己的讀取工具回答；沒有其他 worker，不建立 dispatches。')

    def launch(self, method, data):
        if method != 'brainOpen': raise ValueError('Unsupported native operation')
        args = {'cwd': self.root, **thread_permissions(self.permissions)}
        thread = data.get('threadID')
        if thread:
            result = self.proxy.call('thread/resume', {**args, 'threadId': thread, 'excludeTurns': True})
        else:
            result = self.proxy.call('thread/start', args)
        exact = result['thread']['id']
        if thread and exact != thread: raise ValueError('Codex thread mismatch')
        self.proxy.subscribed.add(exact)
        binding = str(uuid.uuid4())
        self.current = {'id': data['launchID'], 'binding': binding, 'codexThreadID': exact,
                        'tool': 'codex', 'alive': True, 'title': 'Brain · 個人助理',
                        'directory': self.root, 'phase': 'idle', 'runtime': {}}
        # Persist the exact thread before any user turn can be sent.
        self.data.update(thread=exact, binding=binding, launchState='ready')
        self._save()
        return {'opened': True, 'session': data['launchID'], 'binding': binding}

    def rpc_call(self, session, method, params):
        if session['codexThreadID'] != self.data.get('thread'): raise ValueError('Codex thread mismatch')
        if method == 'turn/start':
            params = {**params, **turn_permissions(self.permissions)}
        return self.proxy.call(method, params)

    def state(self):
        session = self.current
        if not session:
            if not self.data.get('thread'): return {'sessions': []}
            session = {'id': self.session_id, 'binding': self.data.get('binding', ''),
                       'codexThreadID': self.data['thread'], 'tool': 'codex',
                       'title': 'Brain · 個人助理', 'directory': self.root}
        thread = session['codexThreadID']
        approvals = self.proxy.approvals(thread)
        events = [e for e in self.proxy.lifecycle if e[0] == thread] if hasattr(self.proxy, 'lifecycle') else []
        active = bool(self.current and events and events[-1][1] == 'turn/started')
        live = bool(self.current and not self.proxy.closed)
        runtime = 'approval' if approvals else 'running' if active else 'idle' if live else 'notLoaded'
        updated = max([self.data.get('openedAt', 0),
                       *[r.get('created', 0) for r in self.data['requests'].values()]])
        notice = 'decision:' + ':'.join(sorted(r['id'] for r in approvals)) if approvals else ''
        return {'sessions': [{**session, 'alive': live, 'readOnly': True,
                             'updatedAt': updated,
                             'unread': bool(approvals), 'noticeID': notice,
                             'noticeAt': updated, 'approvalReviewer': 'user',
                             'noticeReason': 'permission' if approvals else '',
                             'canSend': False, 'canClose': False, 'lifecycleReady': True,
                             'phase': 'waiting' if approvals else 'running' if active else 'idle',
                             'status': '需要你的決定' if approvals else '處理中' if active else '已連接' if live else '已保存，可續接',
                             'runtime': {'state': runtime, 'approvals': approvals, 'canApprove': live}}]}

    def close(self):
        self.proxy.close()
