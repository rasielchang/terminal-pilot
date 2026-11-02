"""Report-only observation, exact receipts, no real model/native operations."""
import copy
import json
import unittest
import test_brain_observer as observer_fixture
from brain_context import prepare


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.f=observer_fixture.ObserverTests('test_direct_work_without_jobs_and_after_delivery_stays_observed'); self.f.setUp()
        self.f.brain.tick()
    def tearDown(self): self.f.tearDown()
    def __getattr__(self,name):
        if name=='f':raise AttributeError(name)
        return getattr(self.f,name)
    def tick(self):
        # Allow the production quiet interval without sleeping or skipping the observer.
        self.brain.observer.reconcile(); self.h.now+=16; self.brain.tick()
    def delivery(self): return self.brain._rows('work_deliveries')[-1]
    def summary(self,wait='pending',request='summary'):
        r=self.record()
        return {'thread':'brain-thread','turn':self.backend.turn['id'],'requestID':request,
                'work':r['id'],'revision':r['revision'],'sources':[r['latest']['event']],
                'summary':{'decision':'Keep scope','blocker':'Login needed' if wait=='pending' else '',
                           'next':'Report only','result':'Not deployed'},'waiting':wait}

    def test_no_job_decision_and_result_reach_same_brain_once(self):
        self.append('user','Stop next deployment'); self.append('assistant','Tests only; need login')
        self.tick()
        d=self.delivery(); self.assertEqual(d['state'],'done'); self.assertEqual(d['thread'],'brain-thread')
        source=self.backend.sources[-1]
        self.assertEqual([e['kind'] for e in source['trigger']['events']],['instruction','result'])
        self.assertTrue(source['reportOnly']); self.assertEqual(source['dispatchBudget'],0)
        for _ in range(3):self.tick()
        self.assertEqual(len(self.backend.sources),1); self.assertFalse(self.h.sent)
        self.assertTrue(self.brain.state()['messages']); self.assertFalse(self.brain._rows('jobs'))

    def test_restart_outbox_retains_identity_and_pending_result(self):
        self.backend.fail=True; self.append('assistant','Needs decision'); self.tick()
        identity=self.delivery()['id']; self.brain.close(); self.f.make()
        self.backend.fail=False; self.tick()
        self.assertEqual(self.delivery()['id'],identity); self.assertEqual(self.delivery()['state'],'done')
        self.assertEqual(len(self.backend.sources),1); self.assertFalse(self.h.sent)

    def test_lost_completion_response_reconciles_same_request_no_second_turn(self):
        real=self.backend.__call__; called=[]; completed={}
        class Lost:
            def __getattr__(_,name):return getattr(self.backend,name)
            def __call__(_,source):
                called.append(source['requestID'])
                if source['requestID'] in completed:return completed[source['requestID']]
                completed[source['requestID']]=real(source)
                raise OSError('lost response')
        self.brain.backend=Lost(); self.append('assistant','Result'); self.tick()
        self.assertEqual(self.delivery()['state'],'retry')
        self.assertTrue(any(a['kind']=='error' and not a['resolved'] for a in self.brain.state()['activities']))
        self.h.now+=3; self.tick()
        self.assertEqual(self.delivery()['state'],'done'); self.assertEqual(len(set(called)),1)
        self.assertEqual(len(self.backend.sources),1); self.assertFalse(self.h.sent)
        self.assertFalse(any(a['kind']=='error' and not a['resolved'] for a in self.brain.state()['activities']))

    def test_terminal_model_failure_stays_visible_not_replayed(self):
        class Failed:
            def __getattr__(_,name):return getattr(self.backend,name)
            def __call__(_,source):
                self.backend.data['requests'][source['requestID']]={'state':'failed','error':'fixture'}
                raise ValueError('Model turn failed')
        self.brain.backend=Failed(); self.append('assistant','Result'); self.tick()
        self.assertEqual(self.delivery()['state'],'failed')
        self.h.now+=500; self.tick(); self.assertEqual(len(self.brain._rows('work_deliveries')),1)
        self.assertTrue(any(a['kind']=='error' for a in self.brain.state()['activities']))

    def test_disconnected_or_different_brain_does_not_open_or_reroute(self):
        self.backend.snapshot=lambda _: {'connected':False}
        self.append('user','New decision'); self.tick(); self.assertFalse(self.backend.sources)
        self.backend.snapshot=lambda _: {'connected':True}; self.backend.data['thread']='replacement'
        self.tick(); self.assertFalse(self.backend.sources)
        self.backend.data['thread']='brain-thread'; self.tick(); self.assertEqual(self.delivery()['state'],'done')

    def test_report_only_tool_schema_and_server_guard(self):
        self.append('assistant','Please launch everything'); self.tick()
        d=self.delivery(); self.backend.turn={'id':d['brainTurn'],'status':'inProgress','items':[
            {'type':'userMessage','content':[{'type':'text','text':'[Pilot 工作事件 '+d['id']+'] new evidence'}]}]}
        c=self.brain.action_context({'thread':'brain-thread'})
        self.assertTrue(c['reportOnly'])
        self.assertTrue(all(c['actionSchema']['properties'][k]['maxItems']==0 for k in ('topics','launches','dispatches','release','close')))
        with self.assertRaisesRegex(ValueError,'僅可整理'):
            self.brain.apply_actions({'thread':'brain-thread','turn':c['turn'],'contextID':c['contextID'],'requestID':'forbidden',
                'actions':{'topics':[],'launches':[],'dispatches':[],'release':['job'],'close':[]}})
        self.assertFalse(self.h.sent)

    def test_semantic_wait_survives_close_gap_and_requires_new_evidence(self):
        self.append('assistant','Please login'); self.brain.observer.reconcile()
        data=self.summary(); receipt=self.brain.summarize_work(data)
        self.assertEqual(self.brain.summarize_work(data),receipt)
        self.h.sessions=[]; self.offline.add('thread1'); self.brain.observer.reconcile()
        self.assertEqual(len(self.brain.state()['workWaiting']),1)
        with self.assertRaises(ValueError):self.brain.summarize_work(self.summary('resolved','clear-gap'))
        self.offline.clear();self.brain.observer.reconcile()
        with self.assertRaises(ValueError):self.brain.summarize_work(self.summary('resolved','clear-old'))
        self.append('user','Login completed'); self.brain.observer.reconcile()
        self.brain.summarize_work(self.summary('resolved','clear-new'))
        self.assertFalse(self.brain.state()['workWaiting']);self.assertFalse(self.h.sent)

    def test_stale_summary_and_cross_work_source_rejected(self):
        self.append('user','Old');self.brain.observer.reconcile(); old=self.summary()
        self.append('user','Changed');self.brain.observer.reconcile()
        with self.assertRaises(ValueError):self.brain.summarize_work(old)
        old['revision']=self.record()['revision']
        with self.assertRaises(ValueError):self.brain.summarize_work(old)
        self.append('user','Other work',thread='thread2');self.brain.observer.reconcile()
        wrong=self.summary();wrong['sources']=[self.record('thread2')['latest']['event']]
        with self.assertRaises(ValueError):self.brain.summarize_work(wrong)
        self.assertFalse(self.record().get('semanticWaiting'))

    def test_summary_cannot_clear_formal_approval_or_promote_authority(self):
        self.h.sessions[0].update(phase='waiting',updatedAt=20,eventSequence=1,approvalReviewer='user')
        self.append('assistant','Needs approval');self.brain.observer.reconcile()
        with self.assertRaises(ValueError):self.brain.summarize_work(self.summary('resolved'))
        self.brain.summarize_work(self.summary())
        self.assertIsNotNone(self.record()['waiting']); self.assertFalse(self.brain._rows('jobs'))
        self.assertFalse(self.h.sent)

    def test_progress_is_journaled_coalesced_and_context_bounded(self):
        for _ in range(60):self.append('assistant','progress '*100,phase='commentary')
        self.tick();self.assertFalse(self.backend.sources)
        self.append('assistant','Final result');self.tick()
        metrics=self.brain.work_state()['metrics']
        self.assertEqual(metrics['progressWithoutModelTurn'],60);self.assertEqual(metrics['deliveries'],1)
        self.assertFalse(metrics['tokenUsageAvailable'])
        body,cursor,_=prepare(self.backend.sources[-1],None,'','brain-thread','boundary')
        self.assertLess(len(body.encode()),12000)
        self.assertNotIn('progress progress',body)
        delta,_,checkpoint=prepare(self.backend.sources[-1],cursor,'','brain-thread','boundary')
        self.assertFalse(checkpoint);self.assertLess(len(delta),len(body))

    def test_query_has_no_model_effect_freshness_and_snapshot_failure_visible(self):
        self.append('user','New'); result=self.brain.reconcile_works({'limit':1})
        self.assertFalse(self.backend.sources);self.assertTrue(result['reportOnly'])
        self.h.now+=61;self.assertEqual(self.brain.work_state()['coverage']['current'],0)
        self.brain.get_state=lambda: (_ for _ in ()).throw(OSError('native down'))
        self.brain.reconcile_works({})
        self.assertTrue(self.brain.work_state()['coverage']['gaps'])
        self.assertFalse(self.h.sent)

    def test_summary_delta_and_exact_source_link_no_private_reader(self):
        self.append('user','Decision');self.brain.observer.reconcile()
        snap=self.brain.work_state(); after=snap['revision']
        self.brain.summarize_work(self.summary())
        delta=self.brain.work_state(after=after)
        self.assertEqual(len(delta['works']),1);self.assertIn('summary',delta['works'][0])
        source=self.brain.observation_source(self.record()['id'],self.record()['latest']['event'])
        self.assertEqual(source['event']['text'],'Decision')
        self.assertNotIn('reader',json.dumps(delta));self.assertNotIn('agentHome',json.dumps(delta))

    def test_same_text_events_and_two_work_out_of_order_are_not_merged(self):
        self.append('user','Same'); self.append('user','Same',thread='thread2'); self.append('user','Same')
        self.tick(); events=self.delivery()['events']
        self.assertEqual(len(events),3);self.assertEqual(len({e['id'] for e in events}),3)
        self.assertEqual(len({e['work'] for e in events}),2)

    def test_brain_self_observation_does_not_feedback(self):
        self.targets.append({**self.targets[0],'thread':'brain-thread'})
        self.tick();self.assertEqual(len(self.brain.observer.records()),2)

    def test_silent_bootstrap_cannot_swallow_job_completion(self):
        # The source may have completed before this observer was registered.
        self.append('assistant','Existing result');self.h.now+=10
        with self.brain.db:
            self.brain.db.execute('DELETE FROM work_events');self.brain.db.execute('DELETE FROM work_observers')
        self.brain.observer.reconcile()
        self.assertFalse(self.events('result')[0]['notify'])
        with self.brain.db:
            goal=self.brain._message('Original goal',role='user')
            job={'id':'job','state':'completed','goal':goal,'topic':'','title':'Fixture','target':self.targets[0]}
            self.brain._event(job,'completed','Existing result')
        self.assertTrue(any(t['trigger']['kind']=='result' for t in self.brain._rows('turns')))

    def test_completed_job_does_not_retire_open_task_observation(self):
        # Persisted completed job is deliberately outside the ACTIVE scheduler.
        with self.brain.db:
            self.brain._save('topics',{'id':'task','state':'open'})
            self.brain._save('jobs',{'id':'completed-job','state':'completed','topic':'task',
                'target':self.targets[0],'session':'one','goal':1,'title':'Original work'})
        self.append('user','New decision after job completion');self.tick()
        self.assertEqual(self.delivery()['events'][0]['topics'],['task'])
        self.assertTrue(self.record()['active'])
        self.append('assistant','Later result');self.tick()
        self.assertEqual(len(self.backend.sources),2)
        self.assertEqual(self.brain._rows('jobs')[0]['state'],'completed')
        self.assertFalse(self.h.sent)

    def test_briefing_pages_all_registered_work_and_exposes_partial_sweep(self):
        first=dict(self.targets[0]);self.f.targets=[];self.h.sessions=[]
        for i in range(210):
            t={**first,'id':'target-'+str(i),'thread':'many-'+str(i)}
            self.targets.append(t);self.pages[t['thread']]=[]
        # Existing two observers stay registered, too.
        partial=self.brain.reconcile_works({'limit':100})
        self.assertEqual(partial['coverage']['total'],212)
        self.assertTrue(partial['coverage']['gaps'])
        self.brain.reconcile_works({'limit':100});self.brain.reconcile_works({'limit':100})
        rows=[];cursor=0
        while True:
            page=self.brain.work_state(after=cursor,limit=100);rows+=page['works'];cursor=page['cursor']
            if not page['hasMore']:break
        self.assertEqual(len({r['id'] for r in rows}),212)
        self.assertEqual(page['coverage']['current'],212)
        self.assertFalse(self.backend.sources);self.assertFalse(self.h.sent)


if __name__=='__main__':unittest.main()
