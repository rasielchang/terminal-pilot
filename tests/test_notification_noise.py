"""Synthetic end-to-end observer -> Brain -> push. Never contacts a provider."""
import copy
import errno
import json
from pathlib import Path
import unittest
from urllib.error import URLError
import test_brain_observer as fixture
from brain_timeline import claude_visible
from brain_tools import failure
from web_push import PushService
from test_push import subscription


class NoiseTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ObserverTests('test_direct_work_without_jobs_and_after_delivery_stays_observed');self.f.setUp()
        self.addCleanup(self.f.tearDown);self.f.brain.tick();self.sent=[]
        self.push=PushService(Path(self.f.temp.name)/'push',lambda:self.f.brain.notification_state(self.f.h.state()),
            'https://fixture.example',sender=lambda _s,p,_k,_u:self.sent.append(p) or 201,
            clock=lambda:self.f.h.now,settle_seconds=0)
        self.push.subscribe(subscription())
    def flush(self):
        self.f.brain.observer.reconcile();self.f.h.now+=16;self.f.brain.tick();self.push.tick()
    def summarize(self,waiting='pending',request='summary'):
        r=self.f.record()
        return self.f.brain.summarize_work({'thread':'brain-thread','turn':self.f.backend.turn['id'],'requestID':request,
            'work':r['id'],'revision':r['revision'],'sources':[r['latest']['event']],
            'summary':{'decision':'Scope retained','blocker':'Please login' if waiting=='pending' else '',
                       'next':'Wait for evidence','result':'Not deployed'},'waiting':waiting})
    def test_claude_tool_use_progress_is_visible_but_never_wakes_model_or_push(self):
        for n in range(20):
            record={'type':'assistant','uuid':'progress-'+str(n),'sessionId':'thread1','requestId':'round-'+str(n),
                'timestamp':'1970-01-01T00:16:42Z','message':{'role':'assistant','stop_reason':'tool_use',
                'content':[{'type':'text','text':'正在查 B 組。'}]}}
            item=claude_visible(record,n,'thread1');self.assertEqual(item['phase'],'commentary')
            self.assertEqual(item['text'],'正在查 B 組。');self.f.pages['thread1'].append(item)
            self.flush()
        self.assertFalse(self.f.backend.sources);self.assertFalse(self.sent)
        self.assertEqual(len(self.f.events('progress')),20)
    def test_summary_and_projection_never_reannounce_old_result(self):
        self.f.append('assistant','Tests passed; stage complete.');self.flush()
        self.assertEqual(len(self.sent),1)
        self.summarize(waiting='unchanged')
        for _ in range(8):self.flush()
        self.assertEqual(len(self.f.backend.sources),1);self.assertEqual(len(self.sent),1)
        self.f.append('user','Change scope: no deployment');self.flush()
        self.assertEqual(len(self.f.backend.sources),1)
        self.assertEqual(self.f.record()['instruction']['text'],'Change scope: no deployment')
        self.assertEqual(len(self.sent),1,'A quiet decision must not generate another push')
        self.assertTrue(self.f.record()['active'],'Worker turn completion is not task closure')
    def test_short_updates_are_coalesced_without_losing_sources(self):
        self.f.append('assistant','Stage one passed');self.f.brain.tick();self.push.tick()
        self.f.h.now+=4;self.f.append('assistant','Tests passed; staged build ready.');self.f.brain.tick();self.push.tick()
        self.assertFalse(self.f.backend.sources)
        self.flush();self.assertEqual(len(self.f.backend.sources),1);self.assertEqual(len(self.sent),1)
        self.assertEqual([e['kind'] for e in self.f.backend.sources[0]['trigger']['events']],['result','result'])
    def test_direct_answer_cancels_old_question_but_new_question_not_deduplicated(self):
        self.f.append('assistant','Please login');self.f.brain.tick();self.summarize()
        self.assertEqual(len(self.f.brain.state()['workWaiting']),1)
        self.f.append('user','已登入');self.flush()
        self.assertFalse(self.f.brain.state()['workWaiting'])
        self.assertEqual(self.f.record()['semanticWaiting']['answerState'],'awaiting_confirmation')
        self.assertEqual(len(self.f.backend.sources),1);self.assertFalse(self.sent)
        self.f.append('assistant','Please choose a new deployment region');self.f.brain.tick();self.push.tick()
        self.assertEqual(len(self.sent),1);self.assertEqual(len(self.f.backend.sources),2)
    def test_unrelated_input_and_plain_ack_do_not_approve_runtime_request(self):
        self.f.append('assistant','Please login');self.f.brain.tick();self.summarize()
        self.f.append('user','What is the current progress?');self.f.brain.observer.reconcile()
        self.assertEqual(len(self.f.brain.state()['workWaiting']),1)
        self.f.h.sessions[0].update(phase='waiting',updatedAt=20,eventSequence=1,approvalReviewer='user',noticeReason='permission',unread=True,noticeID='approval')
        self.f.brain.observer.reconcile();self.f.append('user','好了');self.flush()
        self.assertIsNotNone(self.f.record()['waiting']);self.assertFalse(self.f.h.sent)
    def test_answered_wait_cannot_be_rearmed_by_summary_or_receipt_ack(self):
        self.f.append('assistant','Please login');self.f.brain.tick();self.summarize()
        self.f.append('user','收到');self.f.brain.observer.reconcile()
        self.assertEqual(len(self.f.brain.state()['workWaiting']),1)
        self.f.append('user','已登入');self.f.brain.observer.reconcile()
        with self.assertRaisesRegex(ValueError,'較新的問題'):
            self.summarize(request='stale-pending')
        self.f.append('assistant','Please choose a region');self.f.brain.observer.reconcile()
        self.summarize(request='new-pending')
        self.assertEqual(len(self.f.brain.state()['workWaiting']),1)
    def test_answer_before_first_summary_and_legacy_answer_are_reconciled(self):
        self.f.append('assistant','Please login');self.f.brain.tick()
        self.f.append('user','已登入');self.f.brain.observer.reconcile()
        with self.assertRaisesRegex(ValueError,'較新的問題'):
            self.summarize(request='late-first-summary')
        self.f.append('assistant','Please choose a region');self.f.brain.observer.reconcile();self.summarize(request='new-wait')
        self.f.append('user','A');self.f.brain.observer.reconcile()
        r=self.f.record();r['semanticWaiting'].pop('answeredBy');r['semanticWaiting'].pop('answerState')
        with self.f.brain.db:self.f.brain.observer.save(r)
        self.f.brain.close();self.f.make();self.f.brain.observer.reconcile()
        self.assertFalse(self.f.brain.state()['workWaiting'])
        self.assertEqual(self.f.record()['semanticWaiting']['answerState'],'awaiting_confirmation')
    def test_continuing_updates_have_bounded_delay(self):
        for n in range(8):
            self.f.append('assistant','Stage result '+str(n));self.f.brain.tick();self.f.h.now+=4
        self.assertEqual(len(self.f.backend.sources),1)
        self.assertGreaterEqual(len(self.f.backend.sources[0]['trigger']['events']),7)
    def test_upgrade_receipts_and_internal_events_are_silent(self):
        self.f.append('assistant','Tests passed');self.flush()
        with self.f.brain.db:
            self.f.brain.db.execute('DELETE FROM work_delivery_events')
            self.f.brain._set('workDeliveryCursor',0)
            r=self.f.record()
            self.f.brain.observer.event(r,'synthetic-internal-summary','summary',notify=True)
        self.f.brain.close();self.f.make();self.flush()
        self.assertEqual(len(self.f.backend.sources),1);self.assertEqual(len(self.sent),1)
    def test_urgent_work_bypasses_other_works_quiet_period(self):
        self.f.append('assistant','Tests passed; ready.',thread='thread1');self.f.brain.tick()
        self.f.append('assistant','Build failed; blocked.',thread='thread2',phase='error');self.f.brain.tick();self.push.tick()
        self.assertEqual(len(self.f.backend.sources),1)
        events=self.f.backend.sources[0]['trigger']['events'];self.assertEqual({e['work'] for e in events},{self.f.record('thread2')['id']})
        self.assertEqual(len(self.sent),1);self.flush();self.assertEqual(len(self.sent),2)
    def test_pending_coalescing_and_reported_identity_survive_restart_cursor_replay(self):
        self.f.append('assistant','Tests passed');self.f.brain.tick();self.assertFalse(self.f.backend.sources)
        self.f.brain.close();self.f.make();self.flush()
        self.assertEqual(len(self.f.backend.sources),1)
        with self.f.brain.db:self.f.brain._set('workDeliveryCursor',0)
        self.f.brain.close();self.f.make();self.flush();self.flush()
        self.assertEqual(len(self.f.backend.sources),1);self.assertEqual(len(self.sent),1)
    def test_same_words_different_events_and_same_project_different_work_survive(self):
        self.f.append('assistant','Please choose A or B');self.f.brain.tick();self.push.tick()
        self.f.append('assistant','Please choose A or B',thread='thread2');self.f.brain.tick();self.push.tick()
        self.f.append('assistant','Please choose A or B');self.f.brain.tick();self.push.tick()
        self.assertEqual(len(self.f.backend.sources),3);self.assertEqual(len(self.sent),3)
        sources=[d['events'][0]['id'] for d in self.f.brain._rows('work_deliveries')];self.assertEqual(len(set(sources)),3)
    def test_native_and_brain_same_source_push_once_across_push_restart(self):
        self.f.append('assistant','Tests passed');self.f.brain.observer.reconcile()
        event=self.f.record()['result']['event']
        native={'id':'native','phase':'complete','unread':True,'noticeID':'raw','attentionIDs':[event]}
        state={'sessions':[native]};self.push.state=lambda:copy.deepcopy(state);self.push.tick();self.assertEqual(len(self.sent),1)
        state['sessions']=[{**native,'id':'brain','noticeID':'observer-result'}]
        restored=PushService(Path(self.f.temp.name)/'push',lambda:copy.deepcopy(state),'https://fixture.example',
            sender=lambda _s,p,_k,_u:self.sent.append(p) or 201,clock=lambda:self.f.h.now,settle_seconds=0)
        restored.tick();self.assertEqual(len(self.sent),1)
        state['sessions'][0]['attentionIDs']=[event,'different-question'];restored.tick();self.assertEqual(len(self.sent),2)
    def test_real_selector_native_result_and_formal_wait_share_sources_without_mutation(self):
        self.f.append('assistant','Tests passed')
        self.f.h.sessions[0].update(lastTurn='turn-1',unread=True,noticeID='native-result')
        self.f.brain.observer.reconcile()
        native=self.f.h.state();before=copy.deepcopy(native)
        selected=self.f.brain.notification_state(native)
        self.assertEqual(native,before)
        self.assertTrue(next(s for s in selected['sessions'] if s['id']=='one')['attentionIDs'])
        self.push.tick();self.flush();self.assertEqual(len(self.sent),1)
        self.f.append('assistant','Please approve this tool')
        self.f.h.sessions[0].update(lastTurn='turn-2',activeTurn='turn-2',phase='waiting',noticeID='formal',
            noticeReason='permission',approvalReviewer='user',eventSequence=2,updatedAt=30)
        self.f.brain.observer.reconcile();self.push.tick();self.f.brain.tick();self.push.tick()
        self.assertEqual(len(self.sent),2)
        self.assertFalse(self.f.h.sent)
        self.f.append('assistant','Build failed',phase='error')
        self.f.h.sessions[0].update(lastTurn='turn-3',activeTurn='turn-3',phase='error',noticeID='failure',
            eventSequence=3,updatedAt=40)
        self.f.brain.observer.reconcile();self.push.tick();self.f.brain.tick();self.push.tick()
        self.assertEqual(len(self.sent),3,'The native error and its Brain report share both sources')
    def test_slow_push_poll_includes_both_unread_work_results(self):
        self.f.append('assistant','Please choose A or B');self.f.brain.tick()
        self.f.append('assistant','Build failed',thread='thread2',phase='error');self.f.brain.tick()
        self.push.tick()
        self.assertEqual(len(self.sent),1);self.assertEqual(len(next(iter(self.push.devices.values()))['seenAttention']),2)
        self.f.append('assistant','Please choose a new region');self.f.brain.tick();self.push.tick()
        self.assertEqual(len(self.sent),2);self.assertEqual(len(next(iter(self.push.devices.values()))['seenAttention']),3)
    def test_large_receipt_set_stays_local_and_failed_delivery_does_not_consume_it(self):
        source_ids=['source:'+str(n).zfill(64) for n in range(1000)]
        self.push.state=lambda:{'sessions':[{'id':'brain','unread':True,'phase':'complete',
            'noticeID':'many-sources','attentionIDs':source_ids}]}
        self.push.sender=lambda _s,p,_k,_u:self.sent.append(p) or 503
        self.push.tick()
        self.assertFalse(next(iter(self.push.devices.values())).get('seenAttention'))
        self.assertNotIn('attentionIDs',self.sent[-1]);self.assertLess(len(json.dumps(self.sent[-1]).encode()),2048)
        self.push.next_attempt.clear();self.push.sender=lambda _s,p,_k,_u:self.sent.append(p) or 201
        self.push.tick()
        self.assertEqual(len(next(iter(self.push.devices.values()))['seenAttention']),1000)
        self.assertFalse(self.f.h.sent)
    def test_ordinary_direct_brain_reply_is_still_notified(self):
        self.f.backend.append('assistant','Your requested answer',turn='human-direct',phase='final_answer')
        self.push.tick();self.assertEqual(len(self.sent),1)
    def test_ordinary_ack_is_absorbed_without_model_or_push(self):
        self.f.append('user','執行了');self.flush()
        self.assertEqual(self.f.record()['instruction']['text'],'執行了')
        self.assertFalse(self.f.backend.sources);self.assertFalse(self.sent)
    def test_model_failure_is_actionable_even_without_natural_reply(self):
        class Failed:
            def __getattr__(_,name):return getattr(self.f.backend,name)
            def __call__(_,source):raise OSError('synthetic provider failure')
        self.f.brain.backend=Failed();self.f.append('assistant','Build failed');self.f.brain.tick();self.push.tick()
        self.assertEqual(len(self.sent),1);self.assertFalse(self.f.h.sent)


class DiagnosticsTests(unittest.TestCase):
    def test_permission_refusal_timeout_and_bad_config_are_distinct(self):
        for error in (PermissionError(errno.EPERM,'blocked'),URLError(PermissionError(errno.EACCES,'blocked'))):
            value=failure(error);self.assertEqual(value['code'],'local_permission_denied');self.assertIn('不代表服務離線',value['error'])
        self.assertEqual(failure(URLError(ConnectionRefusedError(errno.ECONNREFUSED,'refused')))['code'],'connection_refused')
        self.assertEqual(failure(URLError(TimeoutError()))['code'],'timeout')
        self.assertEqual(failure(FileNotFoundError('private location'))['code'],'configuration_error')
        self.assertNotIn('private location',failure(FileNotFoundError('private location'))['error'])


if __name__=='__main__':unittest.main()
