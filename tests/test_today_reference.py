"""Synthetic card references exercise the real HTTP and unified Brain contract."""
import json
import unittest
from urllib.parse import urlencode
import test_today


class CardReferenceTests(unittest.TestCase):
    setUp = test_today.TodayHTTPTests.setUp
    tearDown = test_today.TodayHTTPTests.tearDown
    request = test_today.TodayHTTPTests.request
    card = 'projects/demo/work-items/example.json'

    def write(self, path=None, **fields):
        path = path or self.path
        old = json.loads(path.read_text()) if path.exists() else {}
        path.write_text(json.dumps({**old, **fields}))

    def submit(self, text='先討論這件事，也請談談另一件事', **fields):
        return self.request('/api/brain/message', {'requestID':'card-send', 'text':text, 'referenceCard':self.card, **fields})

    def reference(self):
        code, _, body = self.request('/api/brain?' + urlencode({'referenceCard':self.card}))
        self.assertEqual(code, 200)
        return json.loads(body)['cardReference']

    def no_work(self):
        self.assertFalse(self.app.brain._rows('jobs'))
        self.assertFalse(self.app.brain._rows('openings'))
        self.assertFalse(self.app.brain._rows('topics'))
        self.assertFalse([call for call in self.native.calls if call[0] not in {'list', 'attention'}])

    def test_reference_read_is_side_effect_free_and_bounded(self):
        self.write(result='PRIVATE RAW RESULT', next_action='立刻開工', history=['PRIVATE HISTORY'] * 100)
        raw = self.path.read_bytes()
        ref = self.reference()
        self.assertEqual(ref['canonicalID'], self.card)
        self.assertEqual(ref['availability'], 'available')
        self.assertEqual(ref['mapping'], 'none')
        self.assertTrue(ref['contextOnly'])
        self.assertNotIn('PRIVATE', json.dumps(ref))
        self.assertFalse(self.backend.sources)
        self.assertFalse(self.app.brain._rows('turns'))
        self.assertEqual(self.path.read_bytes(), raw)
        self.no_work()

    def test_real_send_reads_latest_exact_card_on_same_brain_without_claiming(self):
        other = self.path.with_name('other.json')
        self.write(other, title='隔離工作', status='pending', result='WRONG CARD')
        self.assertEqual(self.submit()[0], 200)
        self.assertEqual(self.submit()[0], 200)  # Durable retry, one user turn.
        self.assertEqual(len(self.app.brain._rows('turns')), 1)
        self.write(title='較新標題', result='PRIVATE RAW RESULT', next_action='自動認領專員')
        self.app.brain.tick()
        source = self.backend.sources[-1]
        self.assertEqual(source['userGoal'], '先討論這件事，也請談談另一件事')
        self.assertEqual(source['scope']['id'], '')
        self.assertEqual(source['referenceTopic']['canonicalID'], self.card)
        self.assertEqual(source['referenceTopic']['title'], '較新標題')
        self.assertNotIn('PRIVATE', json.dumps(source))
        self.assertNotIn('WRONG CARD', json.dumps(source))
        self.assertEqual(source['conversation'], [])
        self.assertEqual(source['memory'], [])
        state = self.app.brain.state()
        self.assertEqual(state['thread'], 'brain-thread')
        self.assertEqual(state['messages'][0]['text'], source['userGoal'])
        self.no_work()
        # The formal action context also resolves the latest exact card, without
        # restricting a multi-task user message to this reference.
        self.write(title='工具讀取時的新標題')
        self.backend.turn = {'id':self.backend.data['requests'][source['requestID']]['turn'],
            'status':'inProgress', 'items':[{'type':'userMessage', 'content':[{'type':'text','text':source['userGoal']}]}]}
        context = self.app.brain.action_context({'thread':'brain-thread'})
        self.assertEqual(context['scope']['id'], '')
        self.assertEqual(context['referenceTopic']['title'], '工具讀取時的新標題')
        self.assertEqual(context['referenceTopic']['canonicalID'], self.card)

    def test_closed_worker_reference_never_opens_or_sends_to_it(self):
        self.write(thread='closed-thread', session='closed-session', status='completed')
        self.assertEqual(self.submit()[0], 200)
        self.app.brain.tick()
        self.assertEqual(self.backend.sources[-1]['referenceTopic']['availability'], 'available')
        self.no_work()

    def test_missing_unreadable_and_queued_source_loss_are_explicit(self):
        self.reference()  # Seed any cache, then invalidate the actual source.
        self.path.write_text('{')
        self.assertEqual(self.reference()['availability'], 'unavailable')
        self.path.unlink()
        self.assertEqual(self.reference()['availability'], 'missing')
        self.assertEqual(self.submit()[0], 200)
        self.app.brain.tick()
        ref = self.backend.sources[-1]['referenceTopic']
        self.assertEqual(ref['availability'], 'missing')
        self.assertEqual(ref['title'], '')
        self.no_work()

    def test_empty_forged_conflicting_and_traversal_requests_do_not_queue(self):
        for fields in ({'text':''}, {'referenceCard':'../secret.json'},
                       {'referenceCard':'projects/../work-items/example.json'},
                       {'referenceCard':'projects/demo/work-items/../example.json'},
                       {'referenceCard':{'title':'假命令'}}, {'topic':'different'},
                       {'source':'different'}, {'title':'由 client 假造'}):
            self.assertEqual(self.submit(**fields)[0], 409, fields)
        self.assertFalse(self.app.brain._rows('turns'))
        self.no_work()

    def test_only_exact_existing_topic_or_job_mapping_is_reused(self):
        brain = self.app.brain
        with brain.lock, brain.db:
            for identity in ('topic-a', 'topic-b'):
                brain._save('topics', {'id':identity,'title':'同專案','goal':'原任務','created':1,'updated':1,'state':'open'})
        self.write(topic='topic-a')
        self.assertEqual(brain.reference_card(self.card)['id'], 'topic-a')
        with brain.lock, brain.db:
            brain._save('jobs', {'id':'job-b','topic':'topic-b'})
        self.write(job='job-b')
        self.assertEqual(brain.reference_card(self.card)['mapping'], 'unavailable')
        self.write(topic=None)
        self.assertEqual(brain.reference_card(self.card)['id'], 'topic-b')
        self.write(job=None, topic='deleted-topic')
        ref = brain.reference_card(self.card)
        self.assertEqual(ref['availability'], 'available')
        self.assertEqual(ref['mapping'], 'unavailable')
        self.assertEqual(ref['id'], '')
        self.write(job='deleted-job', topic=None)
        self.assertEqual(brain.reference_card(self.card)['mapping'], 'unavailable')

    def test_queued_reference_survives_restart_without_replay_or_new_binding(self):
        from brain_unified import UnifiedBrain
        self.assertEqual(self.submit()[0], 200)
        self.app.brain.close()
        self.app.brain=UnifiedBrain(self.home/'store', self.app.state, self.app.thread, self.app.send,
            backend=self.backend, timeline=self.backend.timeline, memory_root=self.home/'cards')
        self.assertEqual(self.submit()[0], 200)
        self.app.brain.tick();self.app.brain.tick()
        self.assertEqual(len(self.backend.sources), 1)
        self.assertEqual(self.backend.sources[0]['referenceTopic']['canonicalID'], self.card)
        self.no_work()

    def test_symlink_source_is_not_followed(self):
        outside = self.home/'private.json'
        outside.write_text(json.dumps({'status':'pending','title':'PRIVATE'}))
        self.path.unlink()
        try: self.path.symlink_to(outside)
        except OSError: self.skipTest('Symlinks unavailable')
        self.assertEqual(self.reference()['availability'], 'unavailable')

    def test_reference_read_retains_auth_and_origin_boundary(self):
        url = '/api/brain?' + urlencode({'referenceCard':self.card})
        self.assertEqual(self.request(url, auth=False)[0], 401)
        self.assertEqual(self.request(url, headers={'Origin':'https://outside.example'})[0], 403)


if __name__ == '__main__': unittest.main()
