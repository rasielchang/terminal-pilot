"""Synthetic Brain context cost probe: UTF-8 bytes and Pilot-started model turns.

Replays one identical fixture against a chosen checkout (default: this one):

    python tests/measure_context_cost.py --source /path/to/baseline-checkout
    python tests/measure_context_cost.py

The real CodexBrain queue runs against a stub app-server that counts every
Pilot-started model turn (thread/queue/start). Bytes are what the model reads:
queued input, injected per-turn context and the brain_tools stdout of the
context call the tool contract asks for. Not provider tokens or billing. No
network, provider, Terminal, native bridge or real store is used.
"""
import argparse
import copy
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
args = parser.parse_args()
sys.path[:0] = [str(args.source / 'tests'), str(args.source / 'scripts')]

import test_brain_observer as fixture  # noqa: E402  existing local fixture
from test_brain_unified import seed_continuations  # noqa: E402
from brain_codex import CodexBrain  # noqa: E402
from brain_unified import UnifiedBrain  # noqa: E402
from brain_targets import conversation_target  # noqa: E402

BRAIN = {'id': 'brain', 'binding': 'brain-binding', 'tool': 'codex', 'codexThreadID': 'brain-thread',
         'alive': True, 'phase': 'complete', 'windowVerified': True, 'title': 'Brain'}
TEXT = '已完成畫面與資料流程的驗證，保留原有入口與返回位置，整理測試紀錄並列出尚待確認事項。'


class Model:
    """Stub app-server queue. Starts a turn only when Pilot calls queue/start."""
    def __init__(self, clock):
        self.clock, self.queue, self.turns = clock, [], []
        self.started = 0; self.queued = []; self.injected = []; self.contract = []

    def __call__(self, session, method, p):
        if method == 'thread/queue/add':
            q = {'id': 'queue:' + p['clientUserMessageId'], 'clientUserMessageId': p['clientUserMessageId'], 'input': p['input']}
            self.queue.append(q); self.queued.append(sum(len(i.get('text', '').encode()) for i in p['input']))
            return {'queuedSubmission': q}
        if method == 'thread/queue/list': return {'data': copy.deepcopy(self.queue)}
        if method == 'thread/queue/delete':
            self.queue = [q for q in self.queue if q['id'] != p['queuedSubmissionId']]; return {'deleted': True}
        if method == 'thread/inject_items':
            text = p['items'][0]['content'][0]['text']
            (self.injected if text.startswith('Pilot 回合資料') else self.contract).append(len(text.encode()))
            return {}
        if method == 'thread/turns/list':
            n = int(p.get('cursor') or 0)
            return {'data': copy.deepcopy(self.turns[n:n + 1]), 'nextCursor': str(n + 1) if n + 1 < len(self.turns) else None}
        if method == 'thread/queue/start':
            q = next(q for q in self.queue if q['id'] == p['queuedSubmissionId']); self.queue.remove(q)
            turn = {'id': 'turn:' + q['clientUserMessageId'], 'status': 'inProgress', 'startedAt': self.clock(),
                    'items': [{'type': 'userMessage', 'clientId': q['clientUserMessageId'], 'content': q['input']}]}
            self.turns.insert(0, turn); self.started += 1
            return {'turn': turn}
        raise ValueError('unexpected app-server method ' + method)

    def finish(self):
        turn = self.turns[0]
        turn['status'] = 'completed'
        turn['items'].append({'type': 'agentMessage', 'phase': 'final_answer', 'text': '已回覆本人。'})


def build():
    f = fixture.ObserverTests('test_direct_work_without_jobs_and_after_delivery_stays_observed'); f.setUp()
    f.brain.close()
    h = f.h
    projects = [{'id': 'proj-' + str(k), 'name': '專案' + str(k), 'directory': '/fixture/project-' + str(k), 'tool': 'codex'}
                for k in range(12)]
    h.sessions = [{'id': 'w' + str(i), 'title': '工作' + str(i) + '：介面調整與驗收', 'tool': 'codex' if i % 2 == 0 else 'claude',
                   'binding': 'tty-w' + str(i), 'bindingGeneration': 'generation-w' + str(i),
                   ('codexThreadID' if i % 2 == 0 else 'claudeSessionID'): 'thread-w' + str(i),
                   'directory': projects[i // 2]['directory'], 'canSend': True, 'alive': True, 'phase': 'complete',
                   'windowVerified': True, 'updatedAt': 100 + i, 'status': '已完成本輪回覆'} for i in range(24)]
    h.messages = {s['id']: [{'role': 'user', 'text': '請完成' + s['title'] + '並回報。'},
                            {'role': 'assistant', 'text': TEXT * 12, 'phase': 'final_answer'}] for s in h.sessions}
    history = [{'id': '', 'tool': 'codex', 'directory': projects[k % 12]['directory'], 'agentHome': '',
                'thread': 'history-' + str(k), 'mode': 'specific', 'title': '過去工作' + str(k) + '：資料整理與回報', 'updated': k}
               for k in range(200)]
    for target in history:
        target['id'] = conversation_target({'tool': 'codex', 'directory': target['directory'], 'codexThreadID': target['thread']})['id']
    f.targets = [conversation_target(s) for s in h.sessions] + history
    f.pages = {t['thread']: [] for t in f.targets}
    state = lambda: {'sessions': copy.deepcopy(h.sessions + [BRAIN]), 'projects': copy.deepcopy(projects)}
    model = Model(lambda: h.now)
    backend = CodexBrain(f.temp.name, state, lambda *_: {'opened': False}, model, clock=lambda: h.now,
                         context_marker=lambda _session: 'connection:0')
    backend.data.update(session='brain', binding='brain-binding', thread='brain-thread', launchState='ready'); backend._save()
    timeline = lambda _journal, report_turn=None, **_: ({'thread': 'brain-thread', 'settled': False, 'inputs': []}
        if report_turn is not None else {'messages': [], 'hasMore': False, 'cursor': 0, 'latestCursor': 0, 'thread': 'brain-thread'})
    f.brain = UnifiedBrain(f.temp.name, state, h.thread, h.send, backend=backend, timeline=timeline,
        clock=lambda: h.now, launch=lambda data: {'opened': True, 'session': data['launchID'], 'binding': 'pending'},
        targets=lambda: copy.deepcopy(f.targets), describe=lambda _t: {'preview': TEXT * 5},
        observe_page=f.page, memory_root=Path(f.temp.name) / 'brain')
    seed_continuations(f.brain, h.sessions)
    with f.brain.lock, f.brain.db:
        for k in range(80):
            f.brain._save('topics', {'id': 'history:' + str(k), 'title': '歷史事項' + str(k) + '：交付與核對', 'goal': '本人要求' + TEXT,
                                     'state': 'done' if k % 3 else 'open', 'created': k, 'updated': k})
        for k in range(24):
            session = h.sessions[k % 24]
            f.brain._save('jobs', {'id': 'done-' + str(k), 'goal': 0, 'topic': 'fixture:' + session['id'], 'session': session['id'],
                'title': session['title'], 'instruction': '請繼續' + TEXT * 4, 'state': 'completed', 'result': TEXT * 10,
                'detail': '已完成', 'review': 'reported', 'target': conversation_target(session),
                'thread': 'thread-' + session['id'], 'binding': session['binding'], 'created': 50 + k, 'finished': 60 + k})
    # History is registered first, so the live works carry the newest revisions.
    f.brain.observer.reconcile(limit=300)
    for s in h.sessions:
        thread = s.get('codexThreadID') or s.get('claudeSessionID')
        f.append('user', '請完成' + s['title'] + '，完成後整理結果。' + TEXT, thread=thread)
        for _ in range(3): f.append('assistant', '正在檢查。' * 20, thread=thread, phase='commentary')
        ask = '需要你決定是否部署到測試站？' if s['id'] in {'w3', 'w8'} else ''
        f.append('assistant', TEXT * 15 + ask, thread=thread)
    f.brain.observer.reconcile(limit=300)
    return f, model


class Run:
    def __init__(self):
        self.f, self.model = build()
        self.brain = self.f.brain
        self.views = None

    def tick_until(self, done):
        for _ in range(12):
            if done(): return
            self.f.h.now += 1; self.brain.tick()
        raise AssertionError('fixture did not reach the expected turn state')

    def context(self, **scope):
        """What the shipped tool contract asks for. A checkout without exact
        views rejects the extra keys before any read; it then gets the full call."""
        try: value = self.brain.action_context({'thread': 'brain-thread', **scope})
        except ValueError:
            if not scope: raise
            self.views = False
            value = self.brain.action_context({'thread': 'brain-thread'})
        else:
            if scope: self.views = True
        return value, len((json.dumps(value, ensure_ascii=False) + '\n').encode())

    def turn(self, name, text=None, *, report=None, tool=None, apply=None):
        before = {'started': self.model.started, 'queued': len(self.model.queued), 'injected': len(self.model.injected),
                  'contract': len(self.model.contract)}
        known = {t['id'] for t in self.brain._rows('turns')}
        if report: self.brain.request_work_report(report)
        else: self.brain.submit({'requestID': 'owner:' + name, 'text': text})
        self.tick_until(lambda: self.model.turns and self.model.turns[0]['status'] == 'inProgress'
                        and self.model.started > before['started'])
        tool_bytes, tool_calls, applied = 0, 0, None
        if tool is not None:
            value, size = self.context(**tool); tool_bytes += size; tool_calls += 1
            if apply: applied = apply(self, value)
        live = self.model.turns[0]['id']
        identity = next(k for k, r in self.brain.backend.data['requests'].items() if r.get('turn') == live)
        assert identity not in known
        self.model.finish()
        record = lambda: self.brain.backend.data['requests'].get(identity, {})
        self.tick_until(lambda: record().get('state') == 'completed')
        self.f.h.now += 1; self.brain.tick()
        queued = sum(self.model.queued[before['queued']:]); injected = sum(self.model.injected[before['injected']:])
        return {'scenario': name, 'modelTurnsStarted': self.model.started - before['started'], 'toolCalls': tool_calls,
                'queuedInputBytes': queued, 'injectedContextBytes': injected, 'toolOutputBytes': tool_bytes,
                'totalModelInputBytes': queued + injected + tool_bytes,
                'contextCheckpoint': record().get('contextCheckpoint'),
                'toolContractInstallBytes': sum(self.model.contract[before['contract']:]), 'applied': applied}


def launch_new(run, value):
    target = next(t['id'] for t in value['targets'] if t['mode'] == 'new' and t.get('projectID') == 'proj-2')
    actions = {'topics': [{'id': 'new:readme-install', 'title': 'README 安裝步驟', 'goal': '整理 README 的安裝步驟', 'state': 'open'}],
               'dispatches': [], 'launches': [{'target': target, 'topic': 'new:readme-install', 'title': 'README 安裝步驟',
                                                'instruction': '整理 README 的安裝步驟並回報'}], 'release': []}
    data = {'thread': 'brain-thread', 'contextID': value['contextID'], 'turn': value['turn'], 'requestID': 'launch-readme', 'actions': actions}
    receipt = run.brain.apply_actions(data)
    assert run.brain.apply_actions(data) == receipt, 'same requestID must return the same receipt'
    return {'jobs': len(receipt.get('jobs', [])), 'openings': len(receipt.get('openings', []) or []),
            'sameReceiptOnRetry': True}


def measure():
    run = Run()
    try:
        work = lambda session: next(r['id'] for r in run.brain.observer.records() if r['target']['thread'] == 'thread-' + session)
        rows = [run.turn('morning-brief-full', '早安，整理今天全部工作的狀態。', tool={})]
        rows.append(run.turn('general-query', '工作3 現在進度如何？有沒有要我處理的？', tool={'work': work('w3')}))
        r = run.f.record('thread-w4')
        report = {'requestID': 'report-w4', 'session': 'w4', 'thread': 'thread-w4', 'generation': 'generation-w4',
                  'work': r['id'], 'instruction': r['instruction']['event'], 'sources': [r['result']['event']],
                  'confirmUserRequested': True}
        rows.append(run.turn('explicit-report', report=report))
        rows.append(run.turn('after-report-query', '好，那工作4 接下來要做什麼？', tool={'work': work('w4')}))
        # Last, so opening a launch cannot add unrelated turns to earlier rows.
        rows.append(run.turn('dispatch', '在專案2 開一個新對話，整理 README 的安裝步驟。',
                             tool={'view': 'dispatch', 'project': 'proj-2'}, apply=launch_new))
        total = lambda key: sum(row[key] for row in rows)
        return {'source': str(args.source), 'compactViewsSupported': bool(run.views),
                'scenarios': rows, 'totals': {k: total(k) for k in ('modelTurnsStarted', 'toolCalls', 'totalModelInputBytes')},
                'note': 'Synthetic fixture bytes and stub-counted Pilot-started turns; not provider tokens or billing.'}
    finally:
        run.f.tearDown()


if __name__ == '__main__': print(json.dumps(measure(), ensure_ascii=False, indent=2))
