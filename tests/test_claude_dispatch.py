"""Exact Claude paste receipts, initial empty transcripts and reliable queue recovery."""
import copy
import hashlib
import unittest
import test_brain_unified as unified
from brain_service import dispatch_receipt


def wrap(text, identity='ab12'):
    return '<pasted_content id="'+identity+'">\n'+text+'\n</pasted_content id="'+identity+'">'

class ReceiptTests(unittest.TestCase):
    def test_only_exact_complete_claude_paste_envelope_matches(self):
        wire='[Brain job fixture]\nOnly inspect this task.'
        self.assertTrue(dispatch_receipt(wire,wire,'claude'))
        self.assertTrue(dispatch_receipt(wrap(wire),wire,'claude'))
        self.assertTrue(dispatch_receipt(wrap(wire).replace('\n','\r\n'),wire.replace('\n','\r\n'),'claude'))
        for text in (wrap(wire)+' user amendment','Quoted: '+wrap(wire),wrap(wire+' changed'),
                     wrap(wire).replace('</pasted_content id="ab12">','</pasted_content id="other">'),
                     '<quote>'+wire+'</quote>',wrap(wrap(wire))):
            self.assertFalse(dispatch_receipt(text,wire,'claude'),text)
        self.assertFalse(dispatch_receipt(wrap(wire),wire,'codex'))

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=unified.UnifiedTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.h,self.brain=self.f.h,self.f.brain
        for i,s in enumerate(self.h.sessions):
            s.pop('codexThreadID',None);s.update(tool='claude',claudeSessionID='thread'+str(i+1),directory='/fixture/shared',bindingGeneration=s['binding']+'@instance',phase='connected')
        self.h.messages={'one':[],'two':[]}
    def jobs(self):return self.f.jobs()
    def test_actual_paste_receipt_drains_shared_queue_without_replay_after_restart(self):
        self.f.action(self.f.plan());self.brain.tick();self.h.now+=1
        self.f.backend.turn['id']='second-user-turn';self.f.action(self.f.plan(('two',)),request='second');self.brain.tick()
        self.assertEqual(len(self.h.sent),1)
        first=next(j for j in self.jobs() if j['session']=='one');second=next(j for j in self.jobs() if j['session']=='two')
        self.assertEqual(second['queueWait']['reason'],'checkout_busy')
        # The real failure: old build had sent, failed exact match and persisted uncertain.
        self.h.messages['one'][0]['text']=wrap(first['wire'])
        with self.brain.lock,self.brain.db:
            first['state']='uncertain';self.brain._save('jobs',first)
        self.h.messages['one'].append({'role':'user','text':'A direct user clarification'})
        self.f.completed('one','Answer to clarification')
        self.brain._observe_job(second)
        self.assertEqual(next(j for j in self.jobs() if j['id']==second['id'])['queueWait']['job'],first['id'])
        self.brain.close();self.f.make();self.brain=self.f.brain
        self.brain.tick();self.brain.tick();self.brain.tick();self.brain.tick()
        self.assertEqual(len(self.h.sent),2,'Old uncertain command must never be sent again')
        self.assertEqual(self.h.sent[1]['requestID'],'brain-'+second['id'])
        jobs={j['session']:j for j in self.jobs()}
        self.assertEqual(jobs['one']['state'],'completed');self.assertTrue(jobs['one']['manualObservation'])
        self.assertNotIn('queueWait',jobs['two']);self.assertEqual(jobs['two']['state'],'running')
        self.f.completed('two','Second result');self.brain.tick()
        self.assertEqual(next(j for j in self.jobs() if j['session']=='two')['state'],'completed')
    def test_same_claude_thread_successor_accepts_exact_wrapped_predecessor_only(self):
        self.f.action(self.f.plan());self.brain.tick();first=self.jobs()[0]
        self.h.messages['one'][0]['text']=wrap(first['wire'])
        self.h.now+=1;self.f.backend.turn['id']='next-user-turn'
        plan=self.f.plan();plan['dispatches'][0]['instruction']='Second precise task'
        self.f.action(plan,request='next');self.f.completed('one','First done')
        for _ in range(4):self.brain.tick()
        self.assertEqual(len(self.h.sent),2)
        self.assertIn('Second precise task',self.h.sent[1]['text'])
    def test_generation_change_between_context_and_apply_rejects_stale_authority(self):
        context=self.f.context();self.h.sessions[0]['bindingGeneration']='tty1@new-process'
        with self.assertRaisesRegex(ValueError,'已改變'):self.f.action(self.f.plan(),context=context)
        self.assertFalse(self.jobs());self.assertFalse(self.h.sent)
    def test_empty_new_claude_transcript_receives_formal_dispatch(self):
        first=self.f.action(self.f.plan());self.brain.tick()
        self.assertEqual(len(self.h.sent),1)
        job=self.jobs()[0];self.assertEqual(first['jobs'],[job['id']])
        self.assertEqual(self.h.sent[0]['binding'],'tty1')
        self.assertEqual(job['thread'],'thread1');self.assertEqual(job['generation'],'tty1@instance')
        self.h.messages['one'][0]['text']=wrap(job['wire'])
        self.f.completed('one','Received and finished');self.brain.tick()
        self.assertEqual(self.jobs()[0]['state'],'completed');self.assertEqual(len(self.h.sent),1)
    def test_missing_or_unreliable_snapshot_never_asserts_window_closed(self):
        self.f.action(self.f.plan());real=self.brain.get_state
        self.brain.get_state=lambda:{'sessions':[], 'snapshotReliable':False}
        self.brain.tick();self.assertEqual(self.jobs()[0]['state'],'queued');self.assertFalse(self.h.sent)
        self.brain.get_state=lambda:{'sessions':[], 'snapshotReliable':True}
        self.brain.closure_status=lambda record:{'reliable':True,'absent':False,'generation':record['generation']}
        self.brain.tick();self.assertEqual(self.jobs()[0]['state'],'queued');self.assertFalse(self.h.sent)
        self.brain.get_state=real;self.brain.tick();self.assertEqual(len(self.h.sent),1)
    def test_confirmed_same_generation_closure_blocks_without_opening_or_sending(self):
        self.f.action(self.f.plan());self.brain.get_state=lambda:{'sessions':[],'snapshotReliable':True}
        self.brain.closure_status=lambda record:{'reliable':True,'absent':True,'generation':record['generation']}
        self.brain.tick();self.assertEqual(self.jobs()[0]['state'],'blocked')
        self.assertTrue(self.jobs()[0]['closureProof']['absent']);self.assertFalse(self.h.sent);self.assertFalse(self.f.opened)
    def test_stale_closure_proof_or_missing_generation_is_not_closure(self):
        self.f.action(self.f.plan());self.brain.get_state=lambda:{'sessions':[],'snapshotReliable':True}
        self.brain.closure_status=lambda record:{'reliable':True,'absent':True,'generation':'different-instance'}
        self.brain.tick();self.assertEqual(self.jobs()[0]['state'],'queued')
        with self.brain.lock,self.brain.db:
            job=self.jobs()[0];job.pop('generation');self.brain._save('jobs',job)
        self.brain.tick();self.assertEqual(self.jobs()[0]['state'],'queued');self.assertFalse(self.h.sent)
    def test_reused_agent_generation_does_not_deliver_to_same_thread_new_process(self):
        self.f.action(self.f.plan());self.h.sessions[0]['bindingGeneration']='tty1@reused'
        self.brain.tick();self.assertEqual(self.jobs()[0]['state'],'blocked');self.assertFalse(self.h.sent)
    def test_thread_refresh_failure_is_retryable_but_cannot_send_stale_binding(self):
        self.f.action(self.f.plan());original=self.brain.get_thread
        self.brain.get_thread=lambda _:(_ for _ in ()).throw(OSError('temporary failure'))
        self.brain.tick();self.assertEqual(self.jobs()[0]['state'],'queued');self.assertFalse(self.h.sent)
        self.assertEqual(self.jobs()[0]['queueWait']['reason'],'history_unavailable')
        self.brain.get_thread=lambda s:{**original(s),'snapshotReliable':False}
        self.brain.tick();self.assertFalse(self.h.sent)
        self.brain.get_thread=original;self.brain.tick();self.assertEqual(len(self.h.sent),1)
    def test_released_job_and_changed_wire_cannot_recover_as_delivered(self):
        self.f.action(self.f.plan());self.brain.tick();job=self.jobs()[0]
        self.h.messages['one'][0]['text']=wrap(job['wire']+' changed')
        self.h.now+=46;self.brain.tick();self.assertEqual(self.jobs()[0]['state'],'uncertain')
        self.brain.control({'requestID':'release','action':'takeover','job':job['id']})
        self.h.messages['one'][0]['text']=wrap(job['wire']);self.f.completed('one','old result');self.brain.tick()
        self.assertEqual(self.jobs()[0]['state'],'manual');self.assertEqual(len(self.h.sent),1)

if __name__=='__main__':unittest.main()
