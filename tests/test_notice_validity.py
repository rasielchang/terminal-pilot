"""Synthetic source evidence, action projection and exact routing; no live agents."""
import copy
import unittest
import test_brain_observer as observer_fixture
import test_brain_unified as unified_fixture
from brain_notices import notices


class NoticeValidityTests(unittest.TestCase):
    def observer(self):
        f=observer_fixture.ObserverTests();f.setUp();self.addCleanup(f.tearDown);return f
    def projected(self,f):return f.brain.observer.project(f.record())
    def test_partial_inventory_keeps_verified_source_and_formal_wait(self):
        f=self.observer();f.h.sessions[0].update(windowVerified=True,phase='waiting',eventSequence=1,noticeID='approval',status='方便時請核准',runtime={'approvals':[{'id':'a'}]})
        f.h.sessions[1]['windowVerified']=False
        original=f.brain.get_state;f.brain.get_state=lambda:{**original(),'snapshotReliable':False}
        f.append('user','Latest decision');f.brain.observer.reconcile()
        self.assertEqual(f.record()['coverage'],'current');self.assertEqual(f.record()['runtimeCoverage'],'confirmed')
        self.assertEqual(f.record('thread2')['runtimeCoverage'],'unverified')
        self.assertEqual(self.projected(f)['waiting'][0]['kind'],'runtime')
        self.assertEqual(f.events('source_gap'),[])
        count=len(f.events());f.brain.observer.reconcile();self.assertEqual(len(f.events()),count)
        f.h.sessions[0]['unread']=False;f.brain.observer.reconcile()
        self.assertTrue(self.projected(f)['waiting'],'Read is not approval')
        f.h.sessions[0].update(phase='complete',eventSequence=2,runtime={});f.brain.observer.reconcile()
        self.assertFalse(self.projected(f)['waiting']);self.assertFalse(f.h.sent)
    def test_direct_reply_supersedes_wait_without_deleting_source(self):
        f=self.observer();f.brain.observer.reconcile()
        f.append('assistant','請選擇 A 或 B');f.brain.observer.reconcile()
        self.assertEqual(len(self.projected(f)['waiting']),1)
        f.append('user','採用 A，這一步已完成');f.brain.observer.reconcile()
        p=self.projected(f);self.assertFalse(p['waiting']);self.assertTrue(p['waitingHistory'])
        self.assertIn(p['waitingHistory'][0]['validity'],{'answered','superseded'})
        self.assertEqual(len(f.events('result')),1)
        f.brain.close();f.make();f.brain.observer.reconcile();self.assertFalse(self.projected(f)['waiting'])
    def test_conditional_future_approval_and_deferred_wait_are_history(self):
        for text in ('交付已完成。若之後補跑需要你核准，我會再通知。','不催現在接線；方便時請選擇 A 或 B。'):
            with self.subTest(text=text):
                f=self.observer();f.append('assistant',text);f.brain.observer.reconcile()
                self.assertFalse(self.projected(f)['waiting']);self.assertTrue(self.projected(f)['waitingHistory'])
    def test_new_result_and_source_replacement_supersede_old_summary(self):
        f=self.observer();f.append('assistant','請選擇 A 或 B');f.brain.observer.reconcile()
        old=f.record();wait=copy.deepcopy(old['sourceWaiting']);wait.update(kind='semantic',id='summary',source=[wait['id']])
        old['semanticWaiting']=wait;f.brain.observer.save(old)
        f.append('assistant','測試與交付已完成');f.brain.observer.reconcile();self.assertFalse(self.projected(f)['waiting'])
        f.revision='new-file';f.pages['thread1']=[];f.append('assistant','請選擇 C 或 D');f.brain.observer.reconcile()
        p=self.projected(f);self.assertEqual(len(p['waiting']),1);self.assertIn('C',p['waiting'][0]['action'])
        self.assertTrue(any(w['id']=='summary' for w in p['waitingHistory']))
    def test_closed_or_unavailable_wait_is_unconfirmed_not_resolved(self):
        f=self.observer();f.append('assistant','請選擇 A 或 B');f.brain.observer.reconcile()
        f.h.sessions=[];f.brain.observer.reconcile();p=self.projected(f)
        self.assertFalse(p['waiting']);self.assertEqual(p['waitingHistory'][0]['validity'],'unconfirmed')
        self.assertEqual(p['coverage'],'current');self.assertTrue(f.record()['active'])
    def test_repeated_failure_dedup_is_exact_task_target_and_not_ack(self):
        jobs=[{'id':'a','title':'A','state':'blocked','topic':'task-a','target':{'id':'target-a'},'created':1},
              {'id':'b','title':'B','state':'blocked','topic':'task-b','target':{'id':'target-b'},'created':1}]
        activities=[{'id':str(i),'job':job,'kind':'error','text':'失敗，請確認','notificationEligible':True,'unread':False} for i,job in enumerate(('a','a','b'))]
        r=notices(activities,[],jobs,[]);self.assertEqual(len(r['actions']),2)
        jobs.append({**jobs[0],'id':'retry','state':'completed','created':2})
        r=notices(activities,[],jobs,[]);self.assertEqual([a['job'] for a in r['actions']],['b'])
        self.assertTrue(r['history']);self.assertEqual(jobs[0]['state'],'blocked')
    def test_new_failure_text_replaces_old_action_but_preserves_history(self):
        job={'id':'job','title':'Work','state':'blocked','created':1}
        a=[{'id':str(i),'job':'job','kind':'error','text':text,'notificationEligible':True} for i,text in enumerate(('First failure','Second failure'))]
        result=notices(a,[],[job],[])
        self.assertEqual([r['text'] for r in result['actions']],['Second failure'])
        self.assertEqual([r['text'] for r in result['history']],['First failure'])
    def test_closed_receipt_retires_old_failure_without_deleting_activity(self):
        f=unified_fixture.UnifiedTests();f.setUp();self.addCleanup(f.tearDown)
        f.action({'topics':[],'dispatches':[],'launches':[],'release':[],'close':['one']})
        with f.brain.db:
            r=f.brain._rows('closings')[0];r['state']='uncertain';f.brain._save('closings',r)
            f.brain._closing_event(r,'uncertain','Close result needs checking')
        self.assertEqual(len(f.brain.state()['notices']['actions']),1)
        with f.brain.db:r['state']='closed';f.brain._save('closings',r)
        state=f.brain.state();self.assertFalse(state['notices']['actions']);self.assertTrue(state['notices']['history'])
        self.assertFalse(state['activities'][0]['notificationEligible'])
    def test_context_empty_explains_each_exclusion(self):
        f=unified_fixture.UnifiedTests();f.setUp();self.addCleanup(f.tearDown)
        f.h.sessions[0]['windowVerified']=False;f.h.sessions[1]['claudeSessionID']=''
        c=f.context();self.assertFalse(c['sessions'])
        self.assertEqual(c['sessionDiagnostics']['excludedCounts'],{'window_unverified':1,'missing_thread':1})
        original=f.brain.get_state;f.brain.get_state=lambda:{**original(),'snapshotReliable':False}
        f.h.sessions[0].pop('windowVerified');self.assertFalse(f.context()['sessions'])
        f.brain.get_state=original
        f.h.sessions[0]['windowVerified']=True;f.brain.get_thread=lambda _:(_ for _ in ()).throw(OSError('history offline'))
        c=f.context();self.assertIn('history_unavailable',c['sessionDiagnostics']['excludedCounts'])

if __name__=='__main__':unittest.main()
