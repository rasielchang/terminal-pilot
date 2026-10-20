"""Behavioral coverage for independent matter conversations and shared workers."""
import unittest
import test_brain as base
from test_brain import plan


def topic_entry(identity, title, reply='我會在這裡追蹤。', state='open'):
    return {'id': identity, 'title': title, 'goal': title + '，完成後回報驗證與限制。', 'state': state, 'reply': reply}


class TopicTests(unittest.TestCase):
    setUp, tearDown, make = base.BrainTests.setUp, base.BrainTests.tearDown, base.BrainTests.make

    def create(self, title, identity=None):
        return self.brain.create_topic({'requestID': identity or 'create-' + title, 'title': title})['topic']['id']

    def submit(self, text, identity, topic=''):
        return self.brain.submit({'requestID': identity, 'text': text, 'topic': topic})

    def split(self):
        self.h.plans = [{'reply':'兩件事各自處理。', 'release':[],
                         'topics':[topic_entry('new:web','手機斷線'), topic_entry('new:api','退款查詢')],
                         'dispatches':[{'topic':'new:web','session':'one','instruction':'查明手機斷線'},
                                       {'topic':'new:api','session':'two','instruction':'查明退款'}]}]
        self.submit('查手機斷線，也查退款', 'split'); self.brain.tick(); self.brain.tick()
        return {t['title']:t['id'] for t in self.brain.state()['topics']}

    def test_one_instruction_becomes_two_matters_without_two_brains(self):
        topics = self.split()
        self.assertEqual(len(self.h.sent), 2)
        self.assertEqual(len(self.h.prompts), 1)
        self.assertEqual(len(topics), 2)
        for title, identity in topics.items():
            state = self.brain.state(identity)
            self.assertEqual(len(state['jobs']), 1)
            self.assertEqual(state['jobs'][0]['topic'], identity)
            self.assertEqual(state['topic']['title'], title)
        self.assertEqual(set(self.brain.state()['messages'][0]['links']), set(topics.values()))
        # Lost response/restart returns the same user request and does not redispatch.
        self.brain.close(); self.make()
        self.submit('查手機斷線，也查退款', 'split'); self.brain.tick()
        self.assertEqual(len(self.h.sent), 2)
        self.assertEqual(len(self.brain.state()['topics']), 2)

    def test_worker_result_stays_in_own_matter_with_only_short_main_update(self):
        topics = self.split(); identity = topics['手機斷線']
        self.h.messages['one'].append({'role':'assistant', 'phase':'final_answer', 'text':'斷線修正完成，驗證細節很長。'})
        self.h.sessions[0].update(phase='complete', noticeID='phone-result')
        self.h.plans = [plan('修正並驗證完成，尚未部署。')]
        self.brain.tick()
        self.assertIn('尚未部署', self.brain.state(identity)['messages'][-1]['text'])
        self.assertNotIn('尚未部署', self.brain.state()['messages'][-1]['text'])
        self.assertEqual(self.brain.state()['messages'][-1]['links'], [identity])
        self.assertEqual(len(self.brain.state(topics['退款查詢'])['messages']), 1)
        self.assertEqual(self.h.prompts[-1]['scope']['id'], identity)
        self.assertNotIn('退款', self.h.prompts[-1]['userGoal'])
        self.assertEqual({j['topic'] for j in self.h.prompts[-1]['jobs']}, {identity})
        self.assertIn('two', self.h.prompts[-1]['busySessions'])

    def test_scoped_followup_can_use_another_worker_without_changing_matter(self):
        identity = self.create('同一件跨專案的事')
        self.h.plans = [plan('先查網站', ('one','查網站')), plan('再核對 API', ('two','核對API'))]
        self.submit('幫我核對網站與API', 'first', identity); self.brain.tick(); self.brain.tick()
        self.h.messages['one'].append({'role':'assistant','text':'網站查完'}); self.h.sessions[0].update(phase='complete')
        self.brain.tick(); self.brain.tick()
        self.assertEqual([j['topic'] for j in self.brain.state(identity)['jobs']], [identity, identity])
        self.assertEqual(len(self.brain.state()['topics']), 1)

    def test_scope_cannot_release_or_dispatch_another_matter(self):
        topics = self.split(); identity = topics['手機斷線']
        other = next(j for j in self.brain.state()['jobs'] if j['topic'] != identity)
        self.h.plans = [plan('撤回', release=[other['id']])]
        self.submit('先等一下', 'stop', identity); self.brain.tick()
        self.assertEqual(self.brain.state(identity)['messages'][-1]['kind'], 'error')
        self.assertNotEqual(next(j for j in self.brain.state()['jobs'] if j['id']==other['id'])['state'], 'manual')
        self.h.plans = [{'reply':'錯誤跨界', 'release':[], 'topics':[],
                         'dispatches':[{'topic':other['topic'],'session':'two','instruction':'不應送出'}]}]
        self.submit('再問', 'invalid-target', identity); self.brain.tick()
        self.assertEqual(len(self.h.sent), 2)

    def test_invalid_batch_does_not_create_partial_topic(self):
        self.h.plans = [{'reply':'開始','release':[], 'topics':[topic_entry('new:a','不可建立')],
                         'dispatches':[{'topic':'new:a','session':'missing','instruction':'不應執行'}]}]
        self.submit('開始', 'invalid'); self.brain.tick()
        self.assertFalse(self.brain.state()['topics']); self.assertFalse(self.h.sent)

    def test_reading_one_scope_never_clears_another(self):
        first, second = self.create('A'), self.create('B')
        with self.brain.lock, self.brain.db:
            a = self.brain._report('A 的結果', topic=first)
            b = self.brain._report('B 的結果', topic=second)
        self.brain.acknowledge({'through':999999})
        state = self.brain.notification_state(self.h.state())
        self.assertEqual(sum(s['unread'] for s in state['sessions'] if s['id'].startswith('brain:')), 2)
        self.brain.acknowledge({'topic':first, 'through':b})
        notices = {s['id']:s for s in self.brain.notification_state(self.h.state())['sessions']}
        self.assertFalse(notices['brain:'+first]['unread']); self.assertTrue(notices['brain:'+second]['unread'])
        self.assertEqual(self.brain.state(first)['unreadThrough'], a)

    def test_history_pagination_is_complete_and_never_crosses_scopes(self):
        identity = self.create('長對話'); other = self.create('其他')
        with self.brain.lock, self.brain.db:
            for i in range(310): self.brain._message(str(i), topic=identity)
            self.brain._message('不可混進來', topic=other)
        latest = self.brain.state(identity); older = self.brain.state(identity, latest['messages'][0]['id'])
        oldest = self.brain.state(identity, older['messages'][0]['id'])
        combined = oldest['messages'] + older['messages'] + latest['messages']
        self.assertEqual([m['text'] for m in combined], list(map(str, range(310))))
        self.assertTrue(latest['hasMore']); self.assertFalse(oldest['hasMore'])
        with self.assertRaises(ValueError): self.brain.state('missing')

    def test_late_answer_does_not_take_latest_user_message_from_another_scope(self):
        first, second = self.create('A'), self.create('B')
        self.submit('只查 A', 'a', first); self.submit('只查 B', 'b', second)
        self.h.plans = [plan('A 的回覆'), plan('B 的回覆')]
        self.brain.tick(); self.brain.tick()
        self.assertEqual(self.h.prompts[0]['userGoal'], '只查 A')
        self.assertFalse(any('只查 B' in m['text'] for m in self.h.prompts[0]['conversation']))
        self.assertEqual(self.brain.state(first)['messages'][-1]['text'], 'A 的回覆')
        self.assertEqual(self.brain.state(second)['messages'][-1]['text'], 'B 的回覆')

    def test_source_rebinding_preserves_saved_report_without_reading_new_thread(self):
        topics = self.split(); job = self.brain.state(topics['手機斷線'])['jobs'][0]
        with self.brain.lock, self.brain.db:
            stored = self.brain._rows('jobs')[0]; stored['result']='已保存的原始內容'; self.brain._save('jobs',stored)
        self.assertTrue(self.brain.source(job['id'])['available'])
        self.h.sessions[0]['codexThreadID'] = 'changed'
        self.h.messages['one'] = [{'role':'assistant','text':'另一個對話'}]
        source = self.brain.source(job['id'])
        self.assertFalse(source['available']); self.assertFalse(source['messages'])
        self.assertEqual(source['result'], '已保存的原始內容')

    def test_quote_is_a_validated_reference_not_entire_transcript(self):
        topics = self.split(); job = self.brain.state(topics['手機斷線'])['jobs'][0]
        self.brain.submit({'requestID':'quote','text':'說明這個結果','topic':job['topic'],'source':job['id']})
        self.h.plans=[plan('說明')]; self.brain.tick()
        self.assertEqual(self.h.prompts[-1]['reference']['id'], job['id'])
        self.assertNotIn('messages', self.h.prompts[-1]['reference'])
        with self.assertRaises(ValueError): self.brain.submit({'requestID':'bad-quote','text':'問','topic':topics['退款查詢'],'source':job['id']})

    def test_create_topic_is_durable_idempotent_and_does_not_launch(self):
        first = self.create('名稱', 'same'); self.brain.close(); self.make()
        self.assertEqual(self.create('名稱', 'same'), first)
        with self.assertRaises(ValueError): self.create('不同名稱', 'same')
        self.assertFalse(self.h.sent); self.assertFalse(self.h.prompts)

    def test_legacy_migration_preserves_history_and_does_not_rerun_work(self):
        self.h.plans=[plan('執行', ('one','查明'))]; self.submit('舊的目標','old'); self.brain.tick()
        with self.brain.lock, self.brain.db:
            self.brain.db.execute("DELETE FROM meta WHERE key='topicsMigration'")
            job=self.brain._rows('jobs')[0]; job.update(state='completed', result='舊的結果'); self.brain._save('jobs',job)
        self.brain.close(); self.make()
        topics=self.brain.state()['topics']; self.assertEqual(len(topics),1)
        self.assertEqual(topics[0]['title'],'舊的目標')
        self.assertEqual(self.brain.state(topics[0]['id'])['messages'][-1]['text'],'舊的結果')
        self.brain.tick(); self.assertFalse(self.h.sent)
        self.brain.close(); self.make(); self.assertEqual(len(self.brain.state()['topics']),1)

if __name__ == '__main__': unittest.main()
