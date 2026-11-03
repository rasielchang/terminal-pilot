"""Exercise actual action validation/journals using isolated workers, never real IO."""
import copy
import unittest
import test_brain_unified as fixture
from brain_work import BrainWork


class TaskIsolationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.UnifiedTests();self.f.setUp()
    def tearDown(self):self.f.tearDown()
    def new_plan(self, tool='codex'):
        target=next(t for t in self.f.context()['targets'] if t['mode']=='new' and t['tool']==tool)
        return {'topics':[{'id':'new:feature','title':'Independent feature','goal':'Implement the separate feature','state':'open'}],
            'dispatches':[],'release':[], 'launches':[{'target':target['id'],'topic':'new:feature','title':'Independent feature','instruction':'Implement and test only this feature'}]}
    def test_new_task_cannot_resume_existing_conversation(self):
        p=self.new_plan();p['launches'][0]['target']=self.f.targets[0]['id']
        with self.assertRaisesRegex(ValueError,'新工作必須'):self.f.action(p)
        self.assertFalse(self.f.jobs());self.assertFalse(self.f.opened);self.assertFalse(self.f.h.sent)
    def test_different_existing_task_cannot_dispatch_to_another_thread(self):
        p=self.f.plan();p['dispatches'][0]['session']='two'
        with self.assertRaisesRegex(ValueError,'新工作必須'):self.f.action(p)
        self.assertFalse(self.f.jobs())
    def test_action_choices_exclude_unbound_conversations_but_keep_new_workspace(self):
        topic=self.f.brain._topic('fixture:one');topic['workTargets']=[];self.f.brain._save('topics',topic)
        c=self.f.context();schema=c['actionSchema']['properties']
        self.assertNotIn('one',schema['dispatches']['items']['properties']['session']['enum'])
        self.assertNotIn(self.f.targets[0]['id'],schema['launches']['items']['properties']['target']['enum'])
        self.assertTrue(any(t.startswith('workspace:') for t in schema['launches']['items']['properties']['target']['enum']))
    def test_workspace_without_saved_project_can_launch_new_codex_and_claude(self):
        for tool in ['codex','claude']:
            p=self.new_plan(tool);self.assertTrue(p['launches'][0]['target'].startswith('workspace:'))
            receipt=self.f.action(p,request=tool)
            self.f.brain.tick();self.f.brain.tick()
            self.assertEqual(self.f.opened[-1]['mode'],'new');self.assertEqual(self.f.opened[-1]['sessionID'],'')
            job=next(j for j in self.f.jobs() if j['id']==receipt['jobs'][0])
            self.assertTrue(job['thread'].startswith('new-thread-'))
            self.assertIn(job['target']['id'],self.f.brain._topic(job['topic'])['workTargets'])
    def test_new_launch_ignores_empty_thread_and_unrelated_conflicts(self):
        state=self.f.h.state
        self.f.brain.get_state=lambda:{**state(),'bindingConflicts':[{'tool':'codex','thread':''},{'tool':'codex','thread':'thread1'}]}
        p=self.new_plan();self.f.action(p);self.f.brain.tick();self.f.brain.tick()
        self.assertEqual(len(self.f.opened),1);self.assertEqual(self.f.opened[0]['mode'],'new')
        self.assertEqual(len(self.f.h.sent),1)
        self.assertTrue(BrainWork._binding_conflict(self.f.targets[0],self.f.brain.get_state()))
    def test_new_launch_returning_old_thread_is_blocked(self):
        launch=self.f.launch
        def wrong(data):
            result=launch(data);self.f.h.sessions[-1]['codexThreadID']='thread1';return result
        self.f.brain.launch_work=wrong
        self.f.action(self.new_plan());self.f.brain.tick();self.f.brain.tick()
        self.assertEqual(self.f.jobs()[0]['state'],'blocked');self.assertFalse(self.f.h.sent)
    def test_uncertain_new_launch_cannot_fall_back_to_old_thread_after_restart(self):
        def lost(data):self.f.opened.append(data);raise OSError('lost')
        self.f.brain.launch_work=lost
        receipt=self.f.action(self.new_plan());self.f.brain.tick()
        self.f.brain.close();self.f.make()
        topic=receipt['topics']['new:feature']
        p={'topics':[],'dispatches':[],'release':[],'launches':[{'target':self.f.targets[0]['id'],'topic':topic,'title':'Retry','instruction':'Do the original new feature'}]}
        with self.assertRaisesRegex(ValueError,'新工作必須'):self.f.action(p,request='fallback')
        self.f.brain.tick();self.assertEqual(len(self.f.opened),1);self.assertFalse(self.f.h.sent)
    def test_new_launch_receipt_replay_does_not_duplicate(self):
        p=self.new_plan();c=self.f.context()
        first=self.f.action(p,context=c);self.assertEqual(first,self.f.action(p,context=c))
        self.f.brain.tick();self.f.brain.tick();self.assertEqual(len(self.f.opened),1)
    def test_verified_work_remains_usable_when_unrelated_inventory_is_partial(self):
        self.f.h.sessions[0]['windowVerified']=True
        thread=self.f.brain.get_thread;state=self.f.h.state
        self.f.brain.get_thread=lambda sid:{**thread(sid),'snapshotReliable':False}
        self.f.brain.get_state=lambda:{**state(),'snapshotReliable':False}
        self.f.action(self.f.plan());self.f.brain.tick()
        self.assertEqual(len(self.f.h.sent),1)
    def test_unverified_retained_work_cannot_receive_dispatch(self):
        self.f.h.sessions[0]['windowVerified']=False
        with self.assertRaises(ValueError):self.f.action(self.f.plan())
        self.assertFalse(self.f.h.sent)

if __name__=='__main__':unittest.main()
