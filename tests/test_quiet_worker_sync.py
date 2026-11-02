"""Regression shapes from reported manual discussion and Claude Stop metadata.
Only anonymized inputs and isolated stores; never invokes a real worker/model.
"""
import copy
import json
import unittest
from pathlib import Path
import test_brain_observer as fixture
from brain_timeline import claude_visible
from brain_attention import urgent
from web_push import PushService
from test_push import subscription

class QuietWorkerTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ObserverTests('test_direct_work_without_jobs_and_after_delivery_stays_observed');self.f.setUp()
        self.addCleanup(self.f.tearDown);self.f.brain.tick();self.sent=[]
        self.push=PushService(Path(self.f.temp.name)/'push',lambda:self.f.brain.notification_state(self.f.h.state()),
            'https://fixture.example',sender=lambda _s,p,_k,_u:self.sent.append(p) or 201,clock=lambda:self.f.h.now,settle_seconds=0)
        self.push.subscribe(subscription())
    def tick(self):
        self.f.brain.observer.reconcile();self.f.h.now+=16;self.f.brain.tick();self.push.tick()
    def manual(self,thread='thread2'):
        target=next(t for t in self.f.targets if t['thread']==thread)
        with self.f.brain.db:self.f.brain._save('jobs',{'id':'manual-'+thread,'state':'manual','target':target,
            'created':self.f.h.now,'session':'two' if thread=='thread2' else 'one','topic':'','goal':0})
    def claude(self,text,role='assistant',reason='end_turn'):
        rows=self.f.pages['thread2'];n=len(rows);self.f.h.now+=1
        raw={'type':role,'uuid':'claude-'+str(n),'sessionId':'thread2','requestId':'request-'+str(n),
             'timestamp':'1970-01-01T00:16:42Z','message':{'role':role,'stop_reason':reason,'content':[{'type':'text','text':text}]}}
        item=claude_visible(raw,n,'thread2');item['created']=self.f.h.now;rows.append(item);return item
    def test_manual_discussion_and_task_notification_are_journaled_without_rebroadcast(self):
        self.manual()
        self.claude('Do we still test runtime version 1?',role='user',reason=None)
        self.claude('Version 2 is deployed. No errors.')
        self.claude('<task-notification><status>completed</status><summary>Background deployment wait completed</summary></task-notification>',role='user',reason=None)
        self.claude('已開啟三個測試場。\n稍後檢查有沒有發送失敗的紀錄。結果出來再回報。')
        self.tick();self.assertFalse(self.f.backend.sources);self.assertFalse(self.sent)
        r=self.f.record('thread2');self.assertEqual(r['reportMode'] if 'reportMode' in r else self.f.brain.observer.report_mode(r),'direct')
        self.assertIn('runtime version',r['instruction']['text'])
        self.assertEqual(len(self.f.events('tool_notification')),1);self.assertIn('已開啟',r['result']['text'])
        with self.f.brain.db:goal=self.f.brain._message('What changed?',role='user')
        works=self.f.brain._context({'id':'existing-human-turn','goal':goal,'topic':'','trigger':{'kind':'user'}},self.f.h.state())['works']
        row=next(w for w in works if w['id']==r['id']);self.assertTrue(row['syncOnly']);self.assertEqual(row['result']['event'],r['result']['event'])
        self.assertFalse(self.f.h.sent)
    def test_commentary_mentions_failure_without_announcing_and_true_failures_remain(self):
        for text in ('沒有任何錯誤。','檢查有沒有發送失敗的紀錄。','Checking whether the build failed.'):
            self.assertFalse(urgent(text))
        self.manual();self.claude('正在檢查部署失敗的原因',reason='tool_use');self.tick();self.assertFalse(self.f.backend.sources)
        self.claude('部署失敗，無法繼續。');self.tick();self.assertEqual(len(self.f.backend.sources),1)
        self.claude('請登入以解除阻塞');self.tick();self.assertEqual(len(self.f.backend.sources),2)
        self.claude('還差後台開關，開好跟我說。');self.tick();self.assertEqual(len(self.f.backend.sources),3)
    def test_claude_without_native_turn_id_shares_exact_stop_source_receipt(self):
        self.manual();item=self.claude('請登入')
        self.f.h.sessions[1].update(lastTurn='',activeTurn='',phase='complete',unread=True,noticeID='stop-a',updatedAt=self.f.h.now+.1)
        self.f.brain.observer.reconcile();self.push.tick();self.assertEqual(len(self.sent),1)
        self.tick();self.assertEqual(len(self.sent),1)
        r=self.f.record('thread2');receipts=json.loads(self.push.audit_file.read_text())
        self.assertEqual(receipts[-1]['outcome'],'provider_accepted');self.assertIn(r['latest']['event'],receipts[-1]['attentionIDs'])
        self.assertNotIn('subscription',receipts[-1]);self.assertNotIn('請登入',json.dumps(receipts))
        self.f.brain.close();self.f.make();self.push.tick();self.assertEqual(len(self.sent),1)
        self.claude('請選擇新的區域');self.f.h.sessions[1].update(noticeID='stop-b',updatedAt=self.f.h.now+.1)
        self.tick();self.assertEqual(len(self.sent),2)
    def test_claude_stop_fence_rejects_newer_source_stale_binding_and_new_user(self):
        item=self.claude('Please login');s=self.f.h.sessions[1]
        s.update(lastTurn='',activeTurn='',phase='complete',unread=True,noticeID='old-stop',updatedAt=item['created']-1)
        self.f.brain.observer.reconcile()
        def row():return next(s for s in self.f.brain.notification_state(self.f.h.state())['sessions'] if s['id']=='two')
        self.assertFalse(row().get('attentionIDs'))
        s['updatedAt']=item['created']+1;self.f.brain.observer.reconcile();self.assertTrue(row().get('attentionIDs'))
        s['binding']='reused';self.assertFalse(row().get('attentionIDs'));s['binding']='tty2'
        self.claude('A',role='user',reason=None);self.f.brain.observer.reconcile();self.assertFalse(row().get('attentionIDs'))
    def test_direct_and_background_work_same_project_keep_different_reporting_modes(self):
        self.manual('thread2');self.claude('Local discussion answer')
        self.f.append('assistant','Delegated stage complete',thread='thread1');self.tick()
        self.assertEqual(len(self.f.backend.sources),1)
        self.assertEqual({e['work'] for e in self.f.backend.sources[0]['trigger']['events']},{self.f.record('thread1')['id']})
    def test_unsent_old_batch_becomes_quiet_but_uncertain_queue_is_not_cancelled(self):
        self.f.backend.snapshot=lambda _: {'connected':False}
        self.f.append('assistant','Stage complete');self.tick();d=self.f.brain._rows('work_deliveries')[-1]
        self.manual('thread1');self.f.brain.reports.stage()
        self.assertEqual(self.f.brain._rows('work_deliveries')[-1]['state'],'silent');self.assertFalse(self.f.backend.sources)
        self.f.append('assistant','Please login');self.tick();d=self.f.brain._rows('work_deliveries')[-1]
        self.f.backend.data['requests'][d['id']]={'state':'adding'}
        self.f.append('user','已登入');self.tick()
        self.assertEqual(self.f.brain._rows('work_deliveries')[-1]['state'],'queued')
    def test_direct_answer_and_restart_keep_normal_result_silent(self):
        self.f.append('user','Check current settings');self.f.append('assistant','Settings confirmed');self.tick()
        self.f.brain.close();self.f.make();self.tick();self.assertFalse(self.f.backend.sources)
        self.f.append('assistant','Build failed',phase='error');self.tick();self.assertEqual(len(self.f.backend.sources),1)
    def test_delegated_native_suppression_is_not_a_human_read_receipt(self):
        item=self.claude('Delegated stage complete')
        self.f.h.sessions[1].update(lastTurn='',activeTurn='',phase='complete',unread=True,noticeID='owned-stop',updatedAt=self.f.h.now+.1)
        target=next(t for t in self.f.targets if t['thread']=='thread2')
        with self.f.brain.db:self.f.brain._save('jobs',{'id':'owned','state':'completed','session':'two','sourceNotice':'owned-stop',
            'target':target,'created':self.f.h.now,'topic':'','goal':0})
        self.tick();self.assertEqual(len(self.sent),1)
        self.assertEqual(self.sent[0]['session'],'brain')
        self.assertEqual(self.push.audit[-1]['outcome'],'provider_accepted')
    def test_late_source_index_links_native_receipt_without_second_push(self):
        native={'id':'original','noticeID':'stop','unread':True,'phase':'complete'}
        state={'sessions':[native]};self.push.state=lambda:copy.deepcopy(state)
        self.push.tick();self.assertEqual(len(self.sent),1)
        native['attentionIDs']=['source:late']
        state['sessions'].append({**native,'id':'brain','noticeID':'report'})
        self.push.tick();self.assertEqual(len(self.sent),1)
        self.assertEqual(self.push.audit[-1]['outcome'],'linked_existing_receipt')
        # A newer distinct source still alerts.
        native.update(noticeID='stop-next',attentionIDs=['source:new'])
        self.push.tick();self.assertEqual(len(self.sent),2)
    def test_read_original_source_does_not_push_brain_copy(self):
        self.push.state=lambda:{'sessions':[
            {'id':'original','noticeID':'stop','unread':False,'phase':'complete','attentionIDs':['source:read']},
            {'id':'brain','noticeID':'report','unread':True,'phase':'complete','attentionIDs':['source:read']}]}
        self.push.tick();self.assertFalse(self.sent);self.assertEqual(self.push.audit[-1]['outcome'],'source_read')
    def test_queued_job_does_not_reclaim_direct_work_but_confirmed_dispatch_does(self):
        self.manual();r=self.f.record('thread2');target=next(t for t in self.f.targets if t['thread']=='thread2')
        with self.f.brain.db:self.f.brain._save('jobs',{'id':'later','state':'queued','target':target,'created':self.f.h.now+1})
        self.assertEqual(self.f.brain.observer.report_mode(r),'direct')
        with self.f.brain.db:self.f.brain._save('jobs',{'id':'later','state':'running','target':target,'created':self.f.h.now+1})
        self.assertEqual(self.f.brain.observer.report_mode(r),'background')
    def test_quiet_work_context_is_incremental_and_recovers_without_new_model_turn(self):
        from brain_context import prepare
        self.manual();self.claude('Settings confirmed');self.tick()
        source={'requestID':'context-one','trigger':{'kind':'user'},'works':self.f.brain.observer.context_works()}
        first,cursor,_=prepare(source,None,'','brain-thread','boundary')
        self.f.h.now+=10;self.f.brain.observer.reconcile()
        source['requestID']='context-two';source['works']=self.f.brain.observer.context_works()
        delta,_,checkpoint=prepare(source,cursor,'','brain-thread','boundary')
        self.assertFalse(checkpoint);self.assertIn('"works":{"upsert":[],"remove":[]}',delta)
        self.assertLess(len(delta),len(first))
        recovered,_,checkpoint=prepare(source,cursor,'','new-brain-thread','boundary')
        self.assertTrue(checkpoint);self.assertIn('Settings confirmed',recovered)
        self.assertFalse(self.f.backend.sources);self.assertFalse(self.f.h.sent)
    def test_push_audit_failure_and_restart_distinguish_attempt_from_acceptance(self):
        self.push.state=lambda:{'sessions':[{'id':'one','noticeID':'new','unread':True,'phase':'error','attentionIDs':['source:fixture']}]}
        self.push.sender=lambda *_:503;self.push.tick()
        self.assertEqual(self.push.audit[-1]['outcome'],'not_accepted')
        restored=PushService(self.push.store,self.push.state,'https://fixture.example',sender=lambda *_:201,settle_seconds=0)
        restored.tick();self.assertEqual([r['outcome'] for r in restored.audit],['not_accepted','provider_accepted'])
        restored.tick();self.assertEqual(len(restored.audit),2)
        restored.state=lambda:{'sessions':[{'id':'one','noticeID':'uncertain','unread':True,'phase':'error','attentionIDs':['source:uncertain']}]}
        restored.sender=lambda *_:0;restored.tick()
        self.assertEqual(restored.audit[-1]['outcome'],'delivery_unknown')
        self.assertNotIn('source:uncertain',next(iter(restored.devices.values()))['seenAttention'])

if __name__=='__main__':unittest.main()
