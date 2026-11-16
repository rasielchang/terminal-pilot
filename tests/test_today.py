import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from brain_today import TodayCards


class TodayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime(2030, 5, 14, 12).astimezone().timestamp()
        self.view = TodayCards(self.root, lambda: self.now)
        self.base = {'title': '示範工作', 'status': 'in_progress', 'result': '樣本已保存', 'next_action': '核對樣本',
                     'updated_at': self.now - 60, 'agenda': {'date': '2030-05-14', 'rank': 1, 'reason': '本人安排'},
                     'thread': 'thread-a', 'session': 'session-a'}
        self.sessions = [{'id': 'session-a', 'codexThreadID': 'thread-a', 'directory': '/same'},
                         {'id': 'session-b', 'codexThreadID': 'thread-b', 'directory': '/same'}]
    def tearDown(self): self.temp.cleanup()
    def write(self, name='one', **fields):
        path = self.root / ('projects/demo/work-items/' + name + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**self.base, **fields}), encoding='utf-8')
        return path
    def snapshot(self, records=()): return self.view.snapshot(self.sessions, records)
    def test_transition_and_completion_use_card_not_worker_text(self):
        self.write(result='已完成，請核准。')
        self.assertEqual(len(self.snapshot()['groups']['in_progress']), 1)
        self.write(waiting_on_user={'action': '核對樣本', 'source_at': self.now - 60, 'release_condition': '確認樣本'})
        self.assertEqual(len(self.snapshot()['groups']['needs_user']), 1)
        self.write(status='completed', completed_at=self.now, waiting_on_user={'action': '舊問題'})
        data=self.snapshot();self.assertEqual(len(data['groups']['completed']), 1);self.assertFalse(data['groups']['needs_user'])
        self.assertEqual(data['groups']['completed'][0]['releaseCondition'], '')
    def test_day_change_retains_unfinished_without_scheduling_tomorrow(self):
        self.write();self.write('done', status='completed', completed_at=self.now)
        self.now += 86400
        data=self.snapshot()
        self.assertFalse(data['groups']['in_progress']);self.assertFalse(data['groups']['completed'])
        self.assertEqual(data['groups']['waiting'][0]['note'], '尚未排入今天')
    def test_legacy_unselected_and_actual_completion_date(self):
        self.write(agenda={});self.write('old', status='completed', updated_at=self.now, completed_on='2030-05-13')
        self.write('done', status='completed', agenda={}, completed_on='2030-05-14')
        data=self.snapshot();self.assertEqual(data['notScheduled'],1);self.assertEqual(len(data['groups']['completed']),1)
    def test_due_date_remains_visible_without_auto_selection(self):
        self.write(agenda={}, due_on='2030-05-15', asana={'url':'https://app.asana.com/0/example/example','verified_on':'2030-05-14'})
        data=self.snapshot();self.assertEqual(len(data['deadlines']),1);self.assertFalse(data['groups']['in_progress'])
    def test_exact_thread_not_same_project_or_reused_session(self):
        self.write();self.write('two', thread='thread-b', session='session-b')
        targets=[r['target']['session'] for r in self.snapshot()['groups']['in_progress']]
        self.assertEqual(set(targets), {'session-a','session-b'})
        self.sessions[0]['codexThreadID']='replacement'
        self.assertIsNone(self.snapshot()['groups']['in_progress'][0]['target'])
    def test_invalid_json_retains_last_known_result(self):
        path=self.write();self.snapshot();path.write_text('{')
        data=self.snapshot();row=data['groups']['in_progress'][0]
        self.assertTrue(row['stale']);self.assertEqual(row['result'],'樣本已保存');self.assertEqual(len(data['errors']),1)
    def test_disconnected_source_never_means_complete(self):
        self.write(work='work-a')
        row=self.snapshot([{'id':'work-a','coverage':'unavailable','target':{}}])['groups']['in_progress'][0]
        self.assertTrue(row['stale']);self.assertEqual(row['status'],'in_progress')
    def test_new_reply_suppresses_old_action_without_claiming_resolution(self):
        self.write(work='work-a', waiting_on_user={'action':'請回覆','source_at':self.now-60})
        record={'id':'work-a','coverage':'current','instruction':{'source':{'created':self.now}},'target':{}}
        data=self.snapshot([record]);self.assertFalse(data['groups']['needs_user'])
        row=data['groups']['waiting'][0];self.assertTrue(row['unconfirmed']);self.assertEqual(row['waitingFor'],'')
    def test_unknown_status_not_classified_by_substring(self):
        self.write(status='completed_but_actually_unknown')
        row=self.snapshot()['groups']['waiting'][0];self.assertTrue(row['unconfirmed'])
    def test_untrusted_link_and_symlink(self):
        self.write(asana_url='javascript:alert(1)')
        self.assertEqual(self.snapshot()['groups']['in_progress'][0]['reference'],'')
        path=self.root/'projects/demo/work-items/link.json'
        try: path.symlink_to(self.root/'outside.json')
        except OSError: self.skipTest('Symlinks unavailable')
        (self.root/'outside.json').write_text(json.dumps(self.base))
        self.assertIn('projects/demo/work-items/link.json', self.snapshot()['errors'])
    def test_formal_summary_updates_original_without_marking_done_or_moving_schedule(self):
        path=self.write(work='work-a');before=json.loads(path.read_text())
        result=self.view.sync_summary({'id':'work-a'}, {'result':'已完成本輪','next':'核對交付','blocker':'請驗收'}, 'pending', [{'id':'event-new','source':{'created':self.now}}])
        after=json.loads(path.read_text());self.assertEqual(result['state'],'updated')
        self.assertEqual(after['status'],'in_progress');self.assertEqual(after['agenda'],before['agenda'])
        self.assertEqual(after['waiting_on_user']['action'],'請驗收')
        self.now+=10
        self.view.sync_summary({'id':'work-a'}, {'result':'已核對','next':'繼續後續','blocker':''}, 'resolved', [{'id':'event-reply','source':{'created':self.now}}])
        self.assertFalse(self.snapshot()['groups']['needs_user'])
    def test_newer_decision_and_completed_card_win_over_older_summary(self):
        path=self.write(work='work-a', updated_at=self.now)
        before=path.read_bytes()
        data={'result':'舊進度','next':'舊動作','blocker':'舊問題'}
        result=self.view.sync_summary({'id':'work-a'},data,'pending',[{'id':'old','source':{'created':self.now-60}}])
        self.assertEqual(result['state'],'newer_card');self.assertEqual(path.read_bytes(),before)
        self.write(work='work-a',status='completed',completed_at=self.now)
        result=self.view.sync_summary({'id':'work-a'},data,'pending',[{'id':'new','source':{'created':self.now+60}}])
        self.assertEqual(result['state'],'terminal_card');self.assertFalse(self.snapshot()['groups']['needs_user'])
    def test_expired_action_and_missing_ordering_do_not_resurrect_wait(self):
        path=self.write(work='work-a', updated_at=None, status='needs_user', waiting_on_user={'action':'核對', 'expires_at':self.now-1})
        self.assertFalse(self.snapshot()['groups']['needs_user'])
        self.assertIn('過期', self.snapshot()['groups']['waiting'][0]['note'])
        before=path.read_bytes()
        result=self.view.sync_summary({'id':'work-a'}, {'result':'x','next':'y','blocker':'請回覆'},'pending',[{'id':'e','source':{'created':self.now}}])
        self.assertEqual(result['state'],'unverified_card');self.assertEqual(path.read_bytes(),before)
    def test_structured_approval_is_visible_without_worker_text_rules(self):
        self.write()
        self.sessions[0]['runtime']={'approvals':[{'id':'approval-fixture'}]}
        self.assertEqual(len(self.snapshot()['groups']['needs_user']),1)
    def test_ambiguous_thread_cannot_write_two_work_cards(self):
        self.write();self.write('two')
        result=self.view.sync_summary({'id':'work-a','target':{'thread':'thread-a'}},{'result':'x','next':'y','blocker':''},'unchanged',[{'id':'new','source':{'created':self.now}}])
        self.assertEqual(result['state'],'ambiguous')


class TodayHTTPTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTests
        WebTests.setUp(self)
        from brain_unified import UnifiedBrain
        from test_brain_unified import Backend
        self.backend=Backend()
        self.app.brain=UnifiedBrain(self.home/'store',self.app.state,self.app.thread,self.app.send,
            backend=self.backend,timeline=self.backend.timeline,memory_root=self.home/'cards')
        self.path=self.home/'cards/projects/demo/work-items/example.json';self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({'title':'隔離工作','status':'in_progress','updated_at':datetime.now().astimezone().timestamp(),
            'agenda':{'date':datetime.now().astimezone().date().isoformat()},'result':'本機資料'}))
    def tearDown(self):
        from test_web import WebTests
        WebTests.tearDown(self)
    def request(self,*a,**kw):
        from test_web import WebTests
        return WebTests.request(self,*a,**kw)
    def test_authenticated_real_read_chain_and_live_file_update(self):
        self.assertEqual(self.request('/api/brain/today',auth=False)[0],401)
        code,head,body=self.request('/api/brain/today');self.assertEqual(code,200);self.assertIn('no-store',head)
        self.assertEqual(json.loads(body)['groups']['in_progress'][0]['result'],'本機資料')
        card=json.loads(self.path.read_text());card.update(status='completed',completed_at=datetime.now().astimezone().isoformat());self.path.write_text(json.dumps(card))
        code,_,body=self.request('/api/brain');data=json.loads(body)
        self.assertEqual(code,200);self.assertEqual(len(data['today']['groups']['completed']),1)
        self.assertFalse(data['today']['groups']['in_progress']);self.assertFalse(self.backend.sources)
    def test_cross_origin_and_no_write_route(self):
        self.assertEqual(self.request('/api/brain/today',headers={'Origin':'https://outside.example'})[0],403)
        self.assertEqual(self.request('/api/brain/today',data={'status':'completed'})[0],404)
