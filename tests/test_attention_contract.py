"""Accepted attention contract: production scheduler, isolated DB/model/push IO."""
import copy
import json
from pathlib import Path
import unittest
import test_brain_observer as observer_fixture
from test_push import subscription
from web_push import PushService
from brain_attention_store import outcome_id


class AttentionContractTests(unittest.TestCase):
    def setUp(self):
        self.f=observer_fixture.ObserverTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)
        self.b=self.f.brain; self.h=self.f.h; self.backend=self.f.backend
        for s in self.h.sessions:s.update(unread=False,noticeID='',lastTurn='',bindingGeneration=s['binding'])
        self.b.tick(); self.sent=[]
        self.push=PushService(Path(self.f.temp.name)/'push',lambda:self.b.notification_state(self.h.state()),
            'https://fixture.example',sender=lambda _s,p,_k,_u:self.sent.append(p) or 201,clock=lambda:self.h.now,settle_seconds=0)
        self.push.subscribe(subscription())
    def cycle(self,n=1):
        for _ in range(n):self.h.now+=3; self.b.tick(); self.push.tick()
    def restart(self):
        self.b.close();self.f.make();self.b=self.f.brain
        self.push=PushService(self.push.store,lambda:self.b.notification_state(self.h.state()),'https://fixture.example',
            sender=lambda _s,p,_k,_u:self.sent.append(p) or 201,clock=lambda:self.h.now,settle_seconds=0)
    def delegate(self):
        c=self.b.action_context({'thread':'brain-thread'})
        result=self.b.apply_actions({'thread':'brain-thread','turn':c['turn'],'contextID':c['contextID'],'requestID':'delegation',
            'actions':{'topics':[{'id':'fixture:one','title':'Fixture','goal':'Implement and test','state':'open'}],'dispatches':[{'session':'one','topic':'fixture:one','instruction':'Implement and test this fixture'}], 'launches':[],'release':[]}})
        self.b.tick();job=next(j for j in self.b._rows('jobs') if j['id']==result['jobs'][0])
        self.f.append('user',job['wire']);self.b.observer.reconcile();return job
    def complete(self,source_first=True):
        self.h.sessions[0].update(phase='complete',noticeID='completed-one',unread=True,lastTurn='turn-result')
        self.h.messages['one'].append({'role':'assistant','text':'Tested deliverable','phase':'final_answer'})
        def source():
            self.f.append('assistant','Tested deliverable');self.f.pages['thread1'][-1]['turn']='turn-result'
        if source_first:source()
        self.cycle()
        if not source_first:source();self.cycle()
    def counts(self):return {'models':len(self.backend.sources),'pushes':len(self.sent),'brainPushes':sum(p['session']=='brain' for p in self.sent)}

    def test_ten_direct_exchanges_are_synced_without_brain_inference_or_push(self):
        for i in range(10):
            self.f.append('user','Direct instruction '+str(i))
            self.f.append('assistant','Direct answer '+str(i));self.h.sessions[0].update(phase='complete',unread=False,noticeID='read-'+str(i))
            self.cycle()
        self.assertEqual(self.counts(),{'models':0,'pushes':0,'brainPushes':0})
        self.assertEqual(len(self.f.events('instruction')),10);self.assertEqual(len(self.f.events('result')),10)
        self.restart();self.cycle(3);self.assertEqual(self.counts()['models'],0)
        self.assertIn('Direct answer 9',json.dumps(self.b.observer.context_works()))

    def test_scan_recovery_and_summary_projection_do_not_wake_or_push(self):
        native=self.b.get_state;self.b.get_state=lambda:{**native(),'snapshotReliable':False}
        self.cycle(3);self.assertEqual(len(self.f.events('source_gap')),2)
        self.b.get_state=native;self.cycle(5)
        self.assertEqual(self.counts(),{'models':0,'pushes':0,'brainPushes':0})
        with self.b.db:
            r=self.f.record();self.b.observer.event(r,'summary:fixture','summary',notify=False);self.b.observer.save(r)
        self.restart();self.cycle(5);self.assertEqual(self.counts(),{'models':0,'pushes':0,'brainPushes':0})

    def test_same_result_job_first_and_source_first_share_one_model_and_push(self):
        for order in (True,False):
            with self.subTest(source_first=order):
                other=AttentionContractTests();other.setUp()
                try:
                    other.delegate();other.complete(order);other.cycle(3);other.restart();other.cycle(3)
                    self.assertEqual(other.counts(),{'models':1,'pushes':1,'brainPushes':1})
                    job=other.b._rows('jobs')[0];self.assertEqual(other.b.attention.policies()[other.backend.events[-1]['turn']]['attentionIDs'],[outcome_id(job)])
                finally:other.doCleanups()

    def test_native_source_read_clears_brain_and_restart_preserves_it(self):
        self.delegate();self.complete();self.h.sessions[0]['unread']=False;self.cycle()
        brain=next(s for s in self.b.notification_state(self.h.state())['sessions'] if s['id']=='brain')
        self.assertFalse(brain['unread']);self.restart();self.cycle()
        self.assertEqual(len(self.sent),1);self.assertTrue(self.b.attention.is_read(outcome_id(self.b._rows('jobs')[0])))

    def test_brain_read_clears_source_without_approving(self):
        self.delegate();self.complete()
        self.b.acknowledge({'through':self.backend.events[-1]['cursor']})
        state=self.b.notification_state(self.h.state())
        self.assertFalse(any(s.get('unread') for s in state['sessions']))
        self.assertEqual(len(self.h.sent),1)

    def test_formal_wait_is_visible_without_model_and_new_wait_not_deduplicated(self):
        self.delegate();s=self.h.sessions[0]
        for i in (1,2):
            s.update(phase='waiting',noticeID='approval-'+str(i),unread=True,approvalReviewer='user',noticeReason='permission',
                updatedAt=self.h.now+1,eventSequence=i*2,runtime={'approvals':[{'id':'approval-'+str(i)}]})
            self.cycle();self.assertEqual(len(self.backend.sources),0);self.assertEqual(len(self.sent),i)
            s.update(phase='running',unread=False,updatedAt=self.h.now+1,eventSequence=i*2+1,runtime={})
            self.cycle();self.assertFalse(any(x.get('unread') for x in self.b.notification_state(self.h.state())['sessions']))
        self.restart();self.cycle();self.assertEqual(len(self.sent),2)

    def test_auto_review_and_progress_do_not_alert(self):
        s=self.h.sessions[0];s.update(phase='waiting',unread=True,noticeID='auto',approvalReviewer='auto_review',runtime={'approvals':[{'id':'auto'}]})
        self.cycle();self.assertEqual(self.counts()['pushes'],0)
        self.f.append('assistant','Checking deployment logs',phase='commentary')
        s.update(phase='complete',noticeID='progress',runtime={},updatedAt=self.h.now,lastTurn=self.f.pages['thread1'][-1]['turn'])
        self.cycle();self.assertEqual(self.counts()['pushes'],0);self.assertEqual(self.counts()['models'],0)

    def test_orphan_error_does_not_override_current_reply_and_linked_error_resolves(self):
        self.b._activity('Old receipt missing',kind='error',identity='legacy')
        self.backend.append('assistant','A useful direct reply',phase='final_answer')
        state=self.b.notification_state(self.h.state());self.assertFalse(any(s.get('noticeID')=='activity:legacy' for s in state['sessions']))
        self.assertTrue(self.b.attention.activities()[0]['diagnosticOnly'])
        job=self.delegate();job.update(state='uncertain');self.b._save('jobs',job)
        self.b._report('Receipt unknown',kind='error',job=job['id'])
        self.assertTrue(self.b.attention.activities()[-1]['notificationEligible'])
        job.update(state='manual');self.b._save('jobs',job)
        self.assertFalse(self.b.attention.activities()[-1]['notificationEligible'])

    def test_same_text_different_work_results_keep_distinct_identity(self):
        self.delegate();self.complete();first=self.b._rows('jobs')[0]
        second={**first,'id':'another-job','session':'two','target':self.f.targets[1],'sourceNotice':'second','outcomeReport':None,'reviewEvent':None}
        with self.b.db:self.b._save('jobs',second);self.b._event(second,'completed','Tested deliverable')
        self.cycle();self.assertEqual(len(self.backend.sources),2);self.assertEqual(len(self.sent),2)

    def test_long_health_gap_is_one_diagnostic_no_models_and_resolves(self):
        native=self.b.get_state;self.b.get_state=lambda:{**native(),'snapshotReliable':False}
        self.cycle(24);self.assertEqual(self.counts()['models'],0);self.assertEqual(self.counts()['pushes'],1)
        self.b.get_state=native;self.cycle()
        self.assertFalse(any(s.get('unread') for s in self.b.notification_state(self.h.state())['sessions']))

    def test_idle_progress_is_not_a_completed_job_and_question_uses_native_card(self):
        self.delegate();s=self.h.sessions[0]
        for text,phase in [('Checking deployment logs','final_answer'),('正在查 B 組。','commentary')]:
            self.h.messages['one'].append({'role':'assistant','text':text,'phase':phase})
            self.f.append('assistant',text,phase=phase)
            s.update(phase='complete',noticeID='progress',unread=True,lastTurn=self.f.pages['thread1'][-1]['turn'])
            self.cycle()
        self.assertEqual(self.b._rows('jobs')[0]['state'],'observing')
        self.assertEqual(self.counts()['models'],0);self.assertEqual(self.counts()['pushes'],0)
        self.h.messages['one'].append({'role':'assistant','text':'Please choose A or B','phase':'final_answer'})
        self.f.append('assistant','Please choose A or B')
        s.update(noticeID='question',lastTurn=self.f.pages['thread1'][-1]['turn'])
        self.cycle();self.assertEqual(self.b._rows('jobs')[0]['state'],'waiting')
        self.assertEqual(self.counts(),{'models':0,'pushes':1,'brainPushes':0})
        self.assertEqual(len(self.b.state()['workWaiting']),1)

    def test_failed_result_is_blocked_and_one_shared_attention_not_two_badges(self):
        self.delegate();s=self.h.sessions[0]
        self.h.messages['one'].append({'role':'assistant','text':'Build failed; blocked.','phase':'final_answer'})
        self.f.append('assistant','Build failed; blocked.')
        s.update(phase='complete',unread=True,noticeID='failed',lastTurn=self.f.pages['thread1'][-1]['turn'])
        self.cycle()
        self.assertEqual(self.b._rows('jobs')[0]['state'],'blocked')
        self.assertEqual(self.counts(),{'models':1,'pushes':1,'brainPushes':1})
        state=self.b.notification_state(self.h.state())
        self.assertEqual(sum(bool(r.get('unread')) for r in state['sessions']),1)
        self.b.acknowledge({'through':0,'activities':[a['id'] for a in self.b.state()['activities']]})
        self.assertFalse(any(r.get('unread') for r in self.b.notification_state(self.h.state())['sessions']))
        self.assertEqual(self.b._rows('jobs')[0]['state'],'blocked','Read is not resolution')

    def test_cancellation_before_model_start_never_invokes_model(self):
        from attention_fixtures import outcome
        job=outcome(self.f);job['manualObservation']=True
        with self.b.db:self.b._save('jobs',job)
        self.cycle();self.assertEqual(self.counts()['models'],0)
        self.assertTrue(self.b._rows('turns')[0]['quiet'])

    def test_shared_native_read_propagates_without_consuming_new_result(self):
        self.delegate();self.complete();job=self.b._rows('jobs')[0]
        native={**self.h.state(),'attentionRead':[outcome_id(job),'job-outcome:unrelated']}
        self.assertFalse(any(s.get('unread') for s in self.b.notification_state(native)['sessions']))
        self.assertFalse(self.b.attention.is_read('job-outcome:unrelated'))
        self.assertFalse(self.b.state()['topics'][0]['unread'])

    def test_persistent_diagnostic_does_not_mask_later_result(self):
        self.b._activity('Sustained source outage',kind='error',identity='health:fixture')
        self.push.tick();self.assertEqual(len(self.sent),1)
        self.delegate();self.complete()
        self.assertEqual(len(self.sent),2)
        row=next(s for s in self.b.notification_state(self.h.state())['sessions'] if s['id']=='brain')
        self.assertIn('activity:health:fixture',row['attentionIDs'])
        self.assertIn(outcome_id(self.b._rows('jobs')[0]),row['attentionIDs'])
        self.cycle();self.assertEqual(len(self.sent),2)

    def test_upgrade_queued_job_wait_never_starts_a_model(self):
        job=self.delegate();job['state']='waiting'
        with self.b.db:
            self.b._save('jobs',job)
            self.b._save('turns',{'id':'legacy-wait','goal':job['goal'],'topic':job['topic'],'state':'queued',
                'trigger':{'kind':'result','job':job['id'],'outcome':'waiting','reportOnly':True}})
        self.b._plan_one(self.h.state())
        self.assertFalse(self.backend.sources)
        self.assertTrue(next(t for t in self.b._rows('turns') if t['id']=='legacy-wait')['quiet'])

    def test_reused_window_cannot_inherit_old_job_attention_or_read_receipt(self):
        self.delegate();self.complete();job=self.b._rows('jobs')[0]
        native=self.h.state();native['sessions'][0].update(bindingGeneration='different-generation',codexThreadID='different-thread',unread=False)
        state=self.b.notification_state(native)
        source=next(s for s in state['sessions'] if s['id']=='one')
        self.assertNotIn(outcome_id(job),source.get('attentionIDs',[]))
        self.assertFalse(self.b.attention.is_read(outcome_id(job)))
        self.assertTrue(next(s for s in state['sessions'] if s['id']=='brain')['unread'])

if __name__=='__main__':unittest.main()
