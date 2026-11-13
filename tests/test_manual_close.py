"""External closure must release exact jobs, including after a partial restart."""
import unittest
import test_brain_unified as fixture


class ManualCloseTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.UnifiedTests(); self.f.setUp()
        for s in self.f.h.sessions:
            s.update(windowVerified=True, bindingGeneration=s['binding']+'@instance')

    def tearDown(self): self.f.tearDown()

    def partial(self):
        self.f.brain.get_state = lambda: {**self.f.h.state(), 'snapshotReliable': False}

    def proof(self, *, absent=True, reliable=True, stale=False):
        self.f.brain.closure_status = lambda j: {
            'reliable': reliable, 'absent': absent,
            'generation': 'stale' if stale else j['generation']}

    def test_running_close_releases_checkout_after_restart_without_completing(self):
        self.f.h.sessions[1]['directory'] = self.f.h.sessions[0]['directory']
        with self.f.brain.db:
            topic = self.f.brain._topic('fixture:two')
            topic['workTargets'] = [fixture.conversation_target(self.f.h.sessions[1])['id']]
            self.f.brain._save('topics', topic)
        self.f.action(self.f.plan(('one',)))
        self.f.brain.tick()
        self.f.h.now += 1
        self.f.backend.turn['id'] = 'next-test-turn'
        self.f.action(self.f.plan(('two',)), request='second')
        self.f.brain.tick(); self.f.brain.tick()
        self.assertEqual(len(self.f.h.sent), 1)
        first = next(j for j in self.f.jobs() if j['session'] == 'one')
        self.assertEqual(first['state'], 'running')
        self.f.h.sessions = [s for s in self.f.h.sessions if s['id'] != 'one']
        self.f.brain.close(); self.f.make()
        self.partial(); self.proof()
        self.f.brain.tick(); self.f.brain.tick()
        jobs = {j['session']: j for j in self.f.jobs()}
        self.assertEqual(jobs['one']['state'], 'blocked')
        self.assertTrue(jobs['one']['closureProof']['absent'])
        self.assertNotIn('result', jobs['one'])
        self.assertEqual(jobs['one']['wire'], first['wire'])
        self.assertEqual(jobs['two']['state'], 'running')
        self.assertEqual([s['session'] for s in self.f.h.sent], ['one', 'two'])
        self.assertFalse(self.f.opened)

    def test_stale_row_does_not_mask_exact_closure(self):
        self.f.action(self.f.plan()); self.f.brain.tick()
        self.f.h.sessions[0].update(windowVerified=False, phase='unverified', runtime={'state':'running'})
        self.partial(); self.proof(); self.f.brain.tick()
        self.assertEqual(self.f.jobs()[0]['state'], 'blocked')
        self.assertEqual(len(self.f.h.sent), 1)

    def test_unknown_or_wrong_generation_never_releases_running_job(self):
        self.f.action(self.f.plan()); self.f.brain.tick(); self.f.brain.tick()
        self.f.h.sessions.pop(0); self.partial()
        for options in ({'absent':False}, {'reliable':False}, {'stale':True}):
            self.proof(**options); self.f.brain.tick()
            self.assertEqual(self.f.jobs()[0]['state'], 'running')
        self.assertEqual(len(self.f.h.sent), 1)

    def test_verified_live_work_not_blocked_by_unrelated_partial(self):
        self.f.action(self.f.plan()); self.partial(); self.proof()
        self.f.brain.tick()
        self.assertEqual(len(self.f.h.sent), 1)
        self.assertEqual(self.f.jobs()[0]['state'], 'sent')

    def test_completed_result_survives_external_close(self):
        self.f.action(self.f.plan()); self.f.brain.tick()
        self.f.completed('one', 'Persisted result'); self.f.brain.tick()
        self.f.h.sessions.pop(0); self.partial(); self.proof(); self.f.brain.tick()
        self.assertEqual(self.f.jobs()[0]['state'], 'completed')
        self.assertEqual(self.f.jobs()[0]['result'], 'Persisted result')

    def test_manual_codex_and_claude_observation_uses_confirmed_target_on_partial(self):
        for tool in ('codex', 'claude'):
            with self.subTest(tool=tool):
                f = fixture.UnifiedTests(); f.setUp()
                try:
                    s = f.h.sessions[0]
                    s.update(tool=tool, windowVerified=True, claudeSessionID='thread1')
                    with f.brain.db:
                        topic = f.brain._topic('fixture:one')
                        topic['workTargets'] = [fixture.conversation_target(s)['id']]
                        f.brain._save('topics', topic)
                    f.action(f.plan()); f.brain.tick(); f.brain.manual_send('one')
                    f.h.messages['one'].append({'role':'user','text':'Synthetic manual clarification'})
                    f.completed('one', 'Verified result')
                    original = f.brain.get_thread
                    f.brain.get_thread = lambda sid: {**original(sid), 'snapshotReliable':False}
                    f.brain.tick()
                    self.assertEqual(f.jobs()[0]['state'], 'completed')
                finally: f.tearDown()


class ClosedObservationTests(unittest.TestCase):
    def test_exact_closed_runtime_is_ended_but_source_result_survives_restart(self):
        from test_brain_observer import ObserverTests
        f = ObserverTests(); f.setUp()
        try:
            f.h.sessions[0].update(phase='running', windowVerified=True, bindingGeneration='tty1@instance')
            f.append('assistant', 'Saved partial result')
            f.brain.observer.reconcile()
            self.assertEqual(f.record()['status'], 'running')
            f.h.sessions.pop(0)
            f.brain.close(); f.make()
            f.brain.get_state = lambda: {**f.h.state(), 'snapshotReliable':False}
            f.brain.closure_status = lambda r: {'reliable':True,'absent':True,'generation':r['generation']}
            f.brain.observer.reconcile()
            result = f.record()
            self.assertEqual(result['status'], 'ended')
            self.assertEqual(result['windowState'], 'closed')
            self.assertEqual(result['runtimeCoverage'], 'confirmed_closed')
            self.assertEqual(result['result']['text'], 'Saved partial result')
            self.assertTrue(result['active'], 'History registration must survive window closure')
        finally: f.tearDown()


if __name__ == '__main__': unittest.main()
