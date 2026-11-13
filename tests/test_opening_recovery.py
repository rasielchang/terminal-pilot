"""Withdrawal and explicit recovery of an exact, never-delivered opening."""
import copy
import unittest
import test_brain_unified as fixture


class OpeningRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.UnifiedTests(); self.f.setUp()
        c = self.f.context()
        self.target = next(t for t in c['targets'] if t['mode'] == 'new')
        self.plan = {'topics': [], 'dispatches': [], 'release': [], 'launches': [{
            'target': self.target['id'], 'topic': 'fixture:one', 'title': 'Synthetic reopen',
            'instruction': 'Continue the same explicitly authorized work.'}]}
        self.f.action(self.plan, request='original', context=c)
        self.opening = self.f.brain._rows('openings')[0]
        self.job = self.f.jobs()[0]
        self.proofs = []
        self.f.brain.opening_status = self.proof

    def tearDown(self): self.f.tearDown()

    def proof(self, opening):
        self.proofs.append(copy.deepcopy(opening))
        return {'launchID': opening['id'], 'binding': 'old-binding', 'generation': 'old-binding@birth',
                'tool': self.target['tool'], 'directory': self.target['directory'],
                'reliable': True, 'absent': True, 'evidence': 'complete_inventory', 'checkedAt': self.f.h.now}

    def withdraw(self, state='uncertain'):
        with self.f.brain.lock, self.f.brain.db:
            self.opening.update(state=state, session=self.opening['id'] if state != 'queued' else '',
                                binding='old-binding' if state != 'queued' else '')
            self.f.brain._save('openings', self.opening)
        self.f.brain.control({'requestID': 'release', 'action': 'takeover', 'job': self.job['id']})
        self.request = {'requestID': 'recover', 'opening': self.opening['id'], 'job': self.job['id'],
                        'topic': 'fixture:one', 'target': self.target['id'], 'confirmReopen': True,
                        'reason': 'User closed the original window and explicitly requested reopening.'}

    def test_queued_withdrawal_is_final_even_while_paused(self):
        self.withdraw('queued')
        with self.f.brain.db: self.f.brain._set('paused', True)
        self.f.brain._advance_openings(self.f.h.state())
        self.assertEqual(self.f.brain._rows('openings')[0]['state'], 'failed')
        self.assertFalse(self.f.opened)

    def test_closed_unbound_opening_recovery_and_same_request_retry(self):
        self.withdraw()
        # Original failure must not consume the reopen request ID.
        with self.assertRaisesRegex(ValueError, '正在確認'):
            self.f.action(self.plan, request='reopen-v2')
        result = self.f.brain.recover_opening(self.request)
        self.assertEqual(result['status'], 'recovered')
        self.assertEqual(result['proof']['generation'], 'old-binding@birth')
        self.assertEqual(self.f.brain.recover_opening(self.request), result)
        self.assertEqual(len(self.proofs), 1)
        query = {k: self.request[k] for k in ('opening', 'job', 'topic', 'target')}
        self.assertEqual(self.f.brain.recovery_status(query)['receipt'], result)
        self.f.h.now += 1
        self.f.backend.turn['id'] = 'new-authorized-turn'
        receipt = self.f.action(self.plan, request='reopen-v2')
        self.f.brain.tick(); self.f.brain.tick()
        self.assertEqual(len(self.f.opened), 1)
        self.assertEqual(len(self.f.h.sent), 1)
        self.assertEqual(self.f.opened[0]['mode'], 'new')
        self.assertNotEqual(self.f.jobs()[-1]['thread'], '')
        self.assertIn(self.f.jobs()[-1]['target']['id'], self.f.brain._topic('fixture:one')['workTargets'])
        self.assertEqual(receipt['openings'], [self.f.opened[0]['launchID']])

    def test_starting_and_uncertain_withdrawals_never_bind_or_send(self):
        for state in ('starting', 'uncertain'):
            with self.subTest(state=state):
                self.withdraw(state)
                self.f.brain._advance_openings(self.f.h.state())
                self.assertEqual(self.f.brain._rows('openings')[0]['state'], state)
                self.assertEqual(self.f.jobs()[0]['state'], 'manual')
                self.assertFalse(self.f.h.sent)

    def test_live_unknown_wrong_identity_or_generation_remain_fenced(self):
        self.withdraw()
        proof = self.proof(self.opening)
        for change in ({'absent': False}, {'reliable': False}, {'launchID': 'other'},
                       {'binding': 'other'}, {'generation': ''}, {'directory': '/other'}, {'tool': 'other'}):
            with self.subTest(change=change):
                self.f.brain.opening_status = lambda o: {**proof, **change}
                with self.assertRaises(ValueError): self.f.brain.recover_opening(self.request)
                self.assertEqual(self.f.brain._rows('openings')[0]['state'], 'uncertain')
        self.assertFalse(self.f.opened)

    def test_authorization_identity_prior_send_and_release_required(self):
        self.withdraw()
        for change in ({'confirmReopen': False}, {'reason': ''}, {'job': 'other'},
                       {'topic': 'fixture:two'}, {'target': 'other'}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError): self.f.brain.recover_opening({**self.request, **change})
        for change in ({'state': 'opening'}, {'sentAt': 1}, {'thread': 'bound-thread'}, {'binding': 'bound'}):
            with self.subTest(change=change), self.f.brain.db:
                original = self.f.jobs()[0]
                self.f.brain._save('jobs', {**original, **change})
                with self.assertRaises(ValueError): self.f.brain.recover_opening(self.request)
                self.f.brain._save('jobs', original)

    def test_late_thread_cannot_be_adopted_after_withdrawal(self):
        self.withdraw('starting')
        s = {'id': self.opening['id'], 'binding': 'old-binding', 'tool': self.target['tool'],
             'directory': self.target['directory'], 'codexThreadID': 'late-thread',
             'alive': True, 'phase': 'complete'}
        self.f.h.sessions.append(s)
        self.f.h.messages[s['id']] = []
        self.f.brain._advance_openings(self.f.h.state())
        self.assertEqual(self.f.brain._rows('openings')[0]['state'], 'starting')
        self.assertEqual(self.f.jobs()[0]['thread'], '')
        self.assertFalse(self.f.h.sent)

    def test_failed_proof_does_not_consume_request_or_clear_fence(self):
        self.withdraw('starting')
        def unavailable(_): raise OSError('native connection unavailable')
        self.f.brain.opening_status = unavailable
        with self.assertRaises(ValueError): self.f.brain.recover_opening(self.request)
        self.f.brain.opening_status = self.proof
        self.assertEqual(self.f.brain.recover_opening(self.request)['status'], 'recovered')

    def test_recovery_receipt_survives_restart_and_payload_conflicts(self):
        self.withdraw(); first = self.f.brain.recover_opening(self.request)
        self.f.brain.close(); self.f.make()
        self.assertEqual(self.f.brain.recover_opening(self.request), first)
        with self.assertRaises(ValueError):
            self.f.brain.recover_opening({**self.request, 'reason': 'different'})
        self.assertEqual(self.f.brain._rows('openings')[0]['orphanRecovery']['requestID'], 'recover')

    def test_status_is_readonly_and_requires_exact_opening_job_relation(self):
        self.withdraw()
        query = {k: self.request[k] for k in ('opening', 'job', 'topic', 'target')}
        status = self.f.brain.recovery_status(query)
        self.assertTrue(status['readOnly']); self.assertTrue(status['orphanRecoveryAvailable'])
        self.assertEqual(self.f.brain._rows('openings')[0]['state'], 'uncertain')
        with self.assertRaises(ValueError): self.f.brain.recovery_status({**query, 'job': 'other'})


if __name__ == '__main__': unittest.main()
