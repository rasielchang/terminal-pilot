"""Exact compact context views; synthetic fixtures, no model, Terminal or real store."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest

import test_brain_observer as observer_fixture
from brain_context import prepare
from pilot_http import WebServer

LEGACY_KEYS = {'contextID', 'turn', 'scope', 'sessionDiagnostics', 'sessionPolicy', 'continuation', 'explicitReport',
               'reportOnly', 'works', 'referenceTopic', 'actionSchema', 'topics', 'sessions', 'targets', 'jobs',
               'releasableJobs', 'dispatchBudget'}


class ContextViewTests(unittest.TestCase):
    def setUp(self):
        self.f = observer_fixture.ObserverTests('test_direct_work_without_jobs_and_after_delivery_stays_observed')
        self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.b = self.f.brain
        state = self.b.get_state
        self.b.get_state = lambda: {**state(), 'projects': [
            {'id': 'shared', 'name': 'Shared', 'directory': '/fixture/shared', 'tool': 'codex'}]}
        self.b.observer.reconcile()
        self.f.append('user', 'Finish the website only.', thread='thread1')
        self.f.append('assistant', 'Website tests passed。需要你決定是否部署？', thread='thread1')
        self.f.append('user', 'Handle the API.', thread='thread2')
        self.f.append('assistant', 'API PRIVATE RESULT for another topic', thread='thread2')
        self.b.observer.reconcile()
        self.one, self.two = self.f.record('thread1')['id'], self.f.record('thread2')['id']

    def context(self, **scope):
        return self.b.action_context({'thread': 'brain-thread', **scope})

    def apply(self, context, request, session, topic):
        return self.b.apply_actions({'thread': 'brain-thread', 'contextID': context['contextID'], 'turn': context['turn'],
            'requestID': request, 'actions': {'topics': [], 'launches': [], 'release': [],
                                              'dispatches': [{'session': session, 'topic': topic, 'instruction': '只處理 ' + session}]}})

    def test_full_context_is_unchanged_without_scope(self):
        full = self.context()
        self.assertEqual(set(full), LEGACY_KEYS)
        self.assertEqual({w['id'] for w in full['works']}, {self.one, self.two})
        self.assertIn('API PRIVATE RESULT', json.dumps(full, ensure_ascii=False))
        self.assertEqual({s['id'] for s in full['sessions']}, {'one', 'two'})

    def seed_same_folder_jobs(self):
        # Another topic's pending launch in the same folder and the API job.
        workspace = next(t for t in self.b._catalog(self.b.get_state()).values()
                         if t['mode'] == 'new' and t['directory'] == '/fixture/shared')
        two = self.f.record('thread2')['target']
        with self.b.lock, self.b.db:
            for identity, target, state, text in (('launch-other', workspace, 'opening', 'OTHER TOPIC LAUNCH'),
                                                 ('api-job', two, 'running', 'API JOB INSTRUCTION')):
                self.b._save('jobs', {'id': identity, 'goal': 0, 'topic': 'fixture:two', 'session': 'two', 'title': text,
                    'instruction': text, 'state': state, 'result': text, 'detail': text, 'target': target,
                    'thread': target.get('thread', ''), 'binding': '', 'created': 1})

    def test_status_view_has_only_exact_work_latest_result_and_owner_wait(self):
        self.seed_same_folder_jobs()
        view = self.context(work=self.one)
        self.assertEqual(view['view'], {'mode': 'status', 'work': self.one})
        for key in ('targets', 'sessions', 'actionSchema', 'sessionPolicy'): self.assertNotIn(key, view)
        [work] = view['works']
        self.assertEqual(work['id'], self.one)
        self.assertIn('Website tests passed', work['result']['text'])
        self.assertEqual(len(work['waitingOnOwner']), 1)
        self.assertIn('部署', work['waitingOnOwner'][0]['action'])
        self.assertEqual([t['id'] for t in view['topics']], ['fixture:one'])
        encoded = json.dumps(view, ensure_ascii=False)
        for foreign in ('API PRIVATE RESULT', self.two, 'fixture:two', '/fixture/shared', 'OTHER TOPIC', 'API JOB'):
            self.assertNotIn(foreign, encoded)
        self.assertLess(len(encoded.encode()), len(json.dumps(self.context(), ensure_ascii=False).encode()) / 2)
        # The stored contract is still the full source for unchanged validation.
        stored = next(x for x in self.b._rows('action_contexts') if x['id'] == view['contextID'])
        self.assertEqual({s['id'] for s in stored['source']['sessions']}, {'one', 'two'})

    def test_dispatch_view_keeps_exact_routes_receipts_and_apply_validation(self):
        self.seed_same_folder_jobs()
        view = self.context(view='dispatch', work=self.one)
        self.assertEqual(view['jobs'], [])
        self.assertIn('OTHER TOPIC LAUNCH', json.dumps(self.context(), ensure_ascii=False))
        target_one = self.f.record('thread1')['target']['id']
        self.assertEqual([s['id'] for s in view['sessions']], ['one'])
        specific = [t['id'] for t in view['targets'] if t['mode'] == 'specific']
        self.assertEqual(specific, [target_one])
        self.assertTrue(all(t['directory'] == '/fixture/shared' for t in view['targets']))
        self.assertEqual(view['sessionPolicy']['continuations'], {'fixture:one': [target_one]})
        self.assertEqual(view['actionSchema']['properties']['dispatches']['items']['properties']['session']['enum'], ['one'])
        self.assertNotIn('works', view)
        receipt = self.apply(view, 'dispatch-one', 'one', 'fixture:one')
        self.assertEqual(self.apply(view, 'dispatch-one', 'one', 'fixture:one'), receipt)
        with self.assertRaisesRegex(ValueError, '只有同一事情已綁定的原對話'):
            self.apply(self.context(view='dispatch', work=self.one), 'cross', 'two', 'fixture:one')
        with self.assertRaisesRegex(ValueError, '只有同一事情已綁定的原對話'):
            self.apply(self.context(), 'cross-full', 'two', 'fixture:one')

    def test_project_view_requires_exact_identity_never_name_or_path(self):
        status = self.context(project='shared')
        self.assertEqual({w['id'] for w in status['works']}, {self.one, self.two})
        self.assertNotIn('targets', status)
        dispatch = self.context(view='dispatch', project='project:shared')
        self.assertIn('project:shared', {t['id'] for t in dispatch['targets']})
        for guess in ('Shared', '/fixture/shared', 'shared/'):
            with self.assertRaisesRegex(ValueError, '精確專案'): self.context(project=guess)

    def test_invalid_scope_is_rejected_before_any_context_is_stored(self):
        before = len(self.b._rows('action_contexts'))
        for scope, message in (({'work': self.one, 'project': 'shared'}, '一個工作或一個專案'),
                               ({'view': 'status'}, '精確 --work'), ({'view': 'full', 'work': self.one}, '完整 context'),
                               ({'view': 'apply'}, '用途'), ({'work': 'work:missing'}, '找不到這個已綁定工作'),
                               ({'work': ['list']}, '範圍識別')):
            with self.assertRaisesRegex(ValueError, message): self.context(**scope)
        with self.assertRaisesRegex(ValueError, '原對話'): self.b.action_context({'thread': 'brain-thread', 'limit': 1})
        self.assertEqual(len(self.b._rows('action_contexts')), before)

    def test_owner_turn_automatic_source_is_an_exact_index(self):
        self.b.submit({'requestID': 'owner', 'text': '網站工作現在怎樣？'})
        self.b.tick()
        source = self.f.backend.sources[-1]
        self.assertEqual(source['contextView'], 'index')
        self.assertNotIn('targets', source)
        self.assertEqual(source['onDemand']['via'], 'context --dispatch')
        self.assertIn('project:shared', {p['id'] for p in source['projects']})
        self.assertEqual({w['id'] for w in source['works']}, {self.one, self.two})
        one = next(w for w in source['works'] if w['id'] == self.one)
        self.assertEqual(one['waiting'][0]['kind'], 'source'); self.assertNotIn('result', one)
        body, _, _ = prepare({**source, 'userGoal': '', 'conversation': []}, None, '', 'brain-thread', 'boundary')
        for private in ('API PRIVATE RESULT', 'Website tests passed', '/fixture/shared'): self.assertNotIn(private, body)
        self.assertNotIn('"targets":{"upsert"', body)

    def test_tool_contract_routes_ordinary_queries_to_exact_views(self):
        text = self.b.tool_instructions
        for phrase in ('context --work', '--project', '--dispatch', '早報'): self.assertIn(phrase, text)


class ContextViewHTTPTests(unittest.TestCase):
    def test_shipped_cli_sends_exact_scope_and_rejects_misused_flags(self):
        f = ContextViewTests('test_full_context_is_unchanged_without_scope'); f.setUp(); self.addCleanup(f.doCleanups)
        app = SimpleNamespace(brain=f.b, token='synthetic-only', hosts={'127.0.0.1'}, https_origin='', stylesheet_url='/synthetic.css')
        server = WebServer(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def stop(): server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(stop)
        origin = 'http://127.0.0.1:' + str(server.server_port)
        store = Path(f.f.temp.name)
        (store / 'https-target.json').write_text(json.dumps({'target': origin, 'origin': origin}))
        (store / 'web-auth.json').write_text(json.dumps({'token': app.token}))
        tool = Path(__file__).resolve().parents[1] / 'scripts/brain_tools.py'
        env = {**os.environ, 'CODEX_THREAD_ID': 'brain-thread'}
        def cli(*args):
            result = subprocess.run([sys.executable, '-B', str(tool), *args, '--store', str(store)],
                                    capture_output=True, text=True, env=env, check=False)
            return result.returncode, json.loads(result.stdout)
        code, status = cli('context', '--work', f.one)
        self.assertEqual(code, 0); self.assertEqual(status['view'], {'mode': 'status', 'work': f.one})
        code, dispatch = cli('context', '--dispatch', '--project', 'shared')
        self.assertEqual(code, 0); self.assertEqual(dispatch['view']['mode'], 'dispatch'); self.assertIn('actionSchema', dispatch)
        code, full = cli('context')
        self.assertEqual(code, 0); self.assertEqual(set(full), LEGACY_KEYS)
        code, guessed = cli('context', '--project', 'Shared')
        self.assertEqual(code, 1); self.assertEqual(guessed['status'], 409)
        for misuse in (('works', '--dispatch'), ('apply', '--project', 'shared')):
            code, error = cli(*misuse)
            self.assertEqual(code, 1); self.assertEqual(error['code'], 'configuration_error')
        self.assertFalse(f.f.h.sent)


if __name__ == '__main__': unittest.main()
