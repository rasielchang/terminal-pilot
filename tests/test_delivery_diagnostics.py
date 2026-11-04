"""Proven preflight versus ambiguous writes; isolated fake RPC and durable stores."""
import unittest
from pathlib import Path
import test_photos as photo_fixture
import test_brain_unified as unified_fixture
from delivery_status import SendNotStarted,diagnostic
from photo_store import PhotoStore,scope
from photo_delivery import PhotoDelivery

class DeliveryDiagnosticTests(unittest.TestCase):
    def photos(self):
        f=photo_fixture.PhotoTests();f.setUp();self.addCleanup(f.doCleanups);return f
    def unified(self):
        f=unified_fixture.UnifiedTests();f.setUp();self.addCleanup(f.tearDown);return f
    def test_readiness_race_no_receipt_then_same_request_delivers_one_image(self):
        f=self.photos();p=f.photo();rpc=f.app.live.brain_call
        f.app.live.brain_call=lambda *_:(_ for _ in ()).throw(ValueError('image channel not ready'))
        with self.assertRaises(SendNotStarted) as caught:f.send(p)
        self.assertEqual(caught.exception.diagnostic['stage'],'queue_list')
        self.assertEqual(caught.exception.diagnostic['delivery'],'not_started')
        self.assertIsNone(f.app.photos.receipt('send'));self.assertFalse(f.app.live.calls)
        f.app.photo_delivery=PhotoDelivery(f.app);f.app.live.brain_call=rpc
        self.assertTrue(f.send(p)['sent']);self.assertTrue(f.send(p)['sent'])
        self.assertEqual(sum(m=='thread/queue/add' for m,_ in f.app.live.calls),1)
    def test_existing_ambiguous_receipt_never_becomes_safe_retry(self):
        f=self.photos();p=f.photo();f.app.live.lose_add=True;r=f.send(p)
        self.assertEqual(r['diagnostic']['stage'],'queue_add');self.assertEqual(r['diagnostic']['delivery'],'unknown')
        Path(f.app.photos.paths([p],scope(f.s))[0]).unlink();f.s['phase']='waiting'
        f.app.live.brain_call=lambda *_:(_ for _ in ()).throw(OSError('offline'))
        r=f.send(p);self.assertTrue(r['uncertain']);self.assertEqual(r['diagnostic']['stage'],'queue_reconcile')
        self.assertEqual(sum(m=='thread/queue/add' for m,_ in f.app.live.calls),1)
        with self.assertRaises(ValueError):f.send(p,thread='wrong-thread')
    def test_lost_start_stays_accepted_diagnostic_and_no_second_add(self):
        f=self.photos();p=f.photo();f.app.live.lose_start=True;r=f.send(p)
        self.assertTrue(r['sent']);self.assertEqual(r['diagnostic']['stage'],'queue_start')
        self.assertTrue(f.send(p)['sent']);self.assertEqual(sum(m=='thread/queue/add' for m,_ in f.app.live.calls),1)
    def test_missing_file_rejects_formal_launch_before_any_open(self):
        f=self.unified();f.brain.photos=PhotoStore(f.temp.name);f.brain.photo_scope=lambda:'brain-scope'
        p=f.brain.photos.upload({'requestID':'photo','data':photo_fixture.picture()},'brain-scope')['id']
        Path(f.brain.photos.paths([p],'brain-scope')[0]).unlink();f.h.sessions.pop(0)
        plan=f.plan();plan['dispatches']=[];plan['launches']=[{'target':f.targets[0]['id'],'topic':'fixture:one','title':'Original','instruction':'Look','photos':[p]}]
        with self.assertRaisesRegex(ValueError,'照片檔案'):f.action(plan)
        f.brain.tick();self.assertFalse(f.opened);self.assertFalse(f.jobs());self.assertFalse(f.h.sent)
    def test_proven_preflight_retries_same_job_after_restart_without_launch(self):
        f=self.unified();calls=[]
        def unavailable(data):
            calls.append(data);raise SendNotStarted('queue_list',ValueError('not ready'),retryable=True)
        f.brain.send=unavailable;f.action(f.plan());f.brain.tick();job=f.jobs()[0]
        self.assertEqual(job['state'],'queued');self.assertEqual(job['deliveryDiagnostic']['delivery'],'not_started')
        self.assertFalse(f.backend.sources);f.brain.tick();self.assertEqual(len(calls),1)
        f.brain.close();f.make();f.h.now+=3;f.brain.tick()
        self.assertEqual(len(f.jobs()),1);self.assertEqual(f.jobs()[0]['id'],job['id']);self.assertEqual(len(f.h.sent),1)
        self.assertEqual(f.h.sent[0]['requestID'],calls[0]['requestID']);self.assertFalse(f.opened)
        f.brain.tick();self.assertEqual(len(f.h.sent),1)
    def test_manual_intervention_during_readiness_wait_cancels_retry(self):
        f=self.unified();f.brain.send=lambda _:(_ for _ in ()).throw(SendNotStarted('queue_list',ValueError('not ready'),retryable=True))
        f.action(f.plan());f.brain.tick();f.h.messages['one'].append({'role':'user','text':'I will handle this'})
        f.h.now+=60;f.brain.send=f.h.send;f.brain.tick()
        self.assertEqual(f.jobs()[0]['state'],'manual');self.assertFalse(f.h.sent)
    def test_unknown_send_error_keeps_cause_but_never_auto_retries(self):
        f=self.unified();f.h.fail_send=True;f.action(f.plan());f.brain.tick();job=f.jobs()[0]
        self.assertEqual(job['state'],'uncertain');self.assertEqual(job['deliveryDiagnostic']['stage'],'send')
        self.assertIn('connection lost',job['detail']);f.h.now+=600;f.brain.tick();self.assertEqual(len(f.h.sent),1)
    def test_permanent_readiness_failure_stops_once_with_explicit_reason(self):
        f=self.unified();attempts=[]
        def unavailable(data):
            attempts.append(data);raise SendNotStarted('queue_list',ValueError('connection unavailable'),retryable=True)
        f.brain.send=unavailable;f.action(f.plan());f.brain.tick();f.h.now+=121;f.brain.tick()
        job=f.jobs()[0];self.assertEqual(job['state'],'blocked');self.assertIn('兩分鐘',job['detail'])
        f.h.now+=121;f.brain.tick();self.assertEqual(len(attempts),2);self.assertFalse(f.h.sent);self.assertFalse(f.opened)
    def test_diagnostic_masks_credentials_and_local_path(self):
        d=diagnostic(OSError('token=secret api_key=abc Bearer xyz /Users/private/work/file sk-private'),'send')
        for secret in ('secret','abc','xyz','/Users/','sk-private'):self.assertNotIn(secret,d['message'])
        self.assertEqual(d['delivery'],'unknown');self.assertFalse(d['retryable'])

if __name__=='__main__':unittest.main()
