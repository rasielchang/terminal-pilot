import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from brain_unified import UnifiedBrain
from brain_codex import BrainPending
from brain_targets import conversation_target
from test_brain import Harness


class Backend:
    session_id='brain'
    def __init__(self):
        self.data={'thread':'brain-thread','requests':{}}
        self.turn={'id':'direct-turn','status':'inProgress','items':[{'type':'userMessage','content':[{'type':'text','text':'請分別處理網站與 API，完成後整理結果。'}]}]}
        self.events=[];self.sources=[];self.fail=False
    def snapshot(self,sessions):return {'session':self.session_id,'thread':'brain-thread','connected':True}
    def current_turn(self):return copy.deepcopy(self.turn)
    def ensure_tools(self,_):return True
    def __call__(self,source):
        if self.fail:raise BrainPending()
        self.sources.append(copy.deepcopy(source))
        turn='brain-result-'+source['requestID']
        self.data['requests'][source['requestID']]={'turn':turn,'state':'completed'}
        if source['trigger']['kind']=='user':self.append('user',source['userGoal'],turn)
        self.append('assistant', '已整理：'+source['trigger'].get('result','收到你的交代'),turn,'final_answer')
        return {'completed':True,'turn':turn}
    def append(self,role,text,turn='direct-turn',phase=''):
        n=len(self.events)+1
        self.events.append({'id':str(n),'cursor':n,'role':role,'text':text,'turn':turn,'created':n,'phase':phase,'kind':phase})
    def timeline(self,journal=None,before=None,after=None):
        rows=[m for m in self.events if (before is None or m['cursor']<before) and (after is None or m['cursor']>after)]
        return {'messages':copy.deepcopy(rows),'hasMore':False,'cursor':rows[-1]['cursor'] if rows else after or 0,'latestCursor':len(self.events),'thread':'brain-thread'}


class UnifiedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.h=Harness();self.backend=Backend();self.opened=[];self.closed=[]
        for i,s in enumerate(self.h.sessions):s['directory']='/project'+str(i)
        self.targets=[conversation_target(s) for s in self.h.sessions]
        self.make()
    def make(self):
        self.brain=UnifiedBrain(self.temp.name,self.h.state,self.h.thread,self.h.send,backend=self.backend,
            timeline=self.backend.timeline,clock=lambda:self.h.now,launch=self.launch,targets=lambda:self.targets,
            describe=lambda _: {'preview':'原交代','baseline':__import__('hashlib').sha256('上次的交代'.encode()).hexdigest()},close_work=self.close)
    def tearDown(self):self.brain.close();self.temp.cleanup()
    def launch(self,data):
        self.opened.append(data)
        s={'id':data['launchID'],'binding':'new-binding','directory':data['directory'],'tool':data['tool'],
           'codexThreadID':data['sessionID'],'alive':True,'phase':'complete','canSend':True,'title':data['name']}
        self.h.sessions.append(s);self.h.messages[s['id']]=[{'role':'user','text':'上次的交代'}]
        return {'opened':True,'session':s['id'],'binding':s['binding']}
    def close(self,data,preview=False):
        if preview:return {'bindings':[data['binding']]}
        self.closed.append(data);return {'closed':True}
    def context(self):return self.brain.action_context({'thread':'brain-thread'})
    def action(self,actions,request='apply',context=None):
        c=context or self.context()
        return self.brain.apply_actions({'thread':'brain-thread','contextID':c['contextID'],'turn':c['turn'],'requestID':request,'actions':actions})
    def plan(self,sessions=('one',)):
        return {'topics':[{'id':'new:'+s,'title':s,'goal':'本人要求處理 '+s,'state':'open'} for s in sessions],
                'dispatches':[{'session':s,'topic':'new:'+s,'instruction':'只處理 '+s+' 並回報'} for s in sessions], 'launches':[],'release':[]}
    def jobs(self):return self.brain._rows('jobs')
    def completed(self,session,text):
        s=next(s for s in self.h.sessions if s['id']==session);s.update(phase='complete',noticeID='done:'+text)
        self.h.messages[session].append({'role':'assistant','phase':'final_answer','text':text})

    def test_web_and_direct_share_only_thread_events(self):
        self.backend.append('user','直接在 Brain 說的話')
        self.backend.append('assistant','直接回覆進度',phase='commentary')
        self.brain.submit({'requestID':'phone','text':'從手機交代'})
        self.assertEqual(len(self.brain.state()['messages']),2)
        self.assertEqual(self.brain.state()['pending'][0]['text'],'從手機交代')
        self.brain.tick()
        texts=[m['text'] for m in self.brain.state()['messages']]
        self.assertEqual(texts[:3],['直接在 Brain 說的話','直接回覆進度','從手機交代'])
        self.assertFalse(self.brain.state()['pending'])
        self.assertTrue(self.backend.sources[0]['natural'])
        self.assertEqual(self.backend.sources[0]['conversation'],[])
        self.assertEqual([m['text'] for m in self.brain.state(after=2)['messages']],texts[2:])

    def test_direct_tool_dispatch_is_durable_idempotent_and_out_of_order_results_return(self):
        c=self.context();p=self.plan(('one','two'));first=self.action(p,context=c)
        self.assertEqual(self.action(p,context=c),first)
        self.brain.tick();self.assertEqual(len(self.h.sent),2)
        jobs={j['session']:j for j in self.jobs()}
        self.completed('two','API 完成');self.brain.tick()
        self.completed('one','網站完成');self.brain.tick()
        sources=self.backend.sources
        self.assertEqual([s['trigger']['job'] for s in sources],[jobs['two']['id'],jobs['one']['id']])
        self.assertEqual([s['scope']['id'] for s in sources],[jobs['two']['topic'],jobs['one']['topic']])
        self.assertTrue(all(j['brainThread']=='brain-thread' and j['authorityGoal'] for j in self.jobs()))
        self.assertEqual(len(self.brain.state()['messages']),2)
        self.brain.close();self.make();self.assertEqual(self.action(p,context=c),first)
        self.assertEqual(len(self.h.sent),2)

    def test_replay_different_content_or_wrong_turn_cannot_dispatch(self):
        c=self.context();self.action(self.plan(),context=c)
        with self.assertRaises(ValueError):self.action(self.plan(('two',)),context=c)
        self.backend.turn['id']='another-turn'
        with self.assertRaises(ValueError):self.action(self.plan(('two',)),request='new',context=c)
        self.assertEqual(len(self.jobs()),1)

    def test_invalid_action_leaves_plain_response_visible_and_no_partial_jobs(self):
        self.backend.append('assistant','格式錯誤，我會說明並保留你的需求。',phase='final_answer')
        p=self.plan();p['dispatches'].append({'session':'unknown','topic':'new:one','instruction':'不能執行'})
        with self.assertRaises(ValueError):self.action(p)
        self.assertFalse(self.jobs());self.assertIn('格式錯誤',self.brain.state()['messages'][0]['text'])

    def test_closed_exact_resume_and_source_reopen_never_replays_dispatch(self):
        self.h.sessions.pop(0);p=self.plan();p['dispatches']=[]
        p['launches']=[{'target':self.targets[0]['id'],'topic':'new:one','title':'原網站工作','instruction':'接著完成網站'}]
        self.action(p);self.brain.tick();self.brain.tick()
        self.assertEqual(self.opened[0]['sessionID'],'thread1');self.assertEqual(len(self.h.sent),1)
        job=self.jobs()[0];self.completed(job['session'],'網站結果');self.brain.tick()
        self.h.sessions=[s for s in self.h.sessions if s['id']!=job['session']]
        req={'requestID':'read-original','job':job['id']}
        first=self.brain.open_work(req);self.assertEqual(first,self.brain.open_work(req))
        self.brain.tick();self.brain.tick();self.assertEqual(len(self.h.sent),1)
        self.assertEqual(self.opened[-1]['sessionID'],'thread1')

    def test_waiting_and_error_are_returned_to_brain_without_approval(self):
        self.action(self.plan());self.brain.tick()
        s=self.h.sessions[0];s.update(phase='waiting',runtime={'approvals':[{'id':'consent'}]})
        self.brain.tick();self.assertEqual(self.backend.sources[-1]['trigger']['outcome'],'waiting')
        count=len(self.backend.sources);self.brain.tick();self.assertEqual(len(self.backend.sources),count)
        s.update(phase='error',runtime={});self.brain.tick()
        self.assertEqual(self.backend.sources[-1]['trigger']['outcome'],'blocked')
        self.assertEqual(len(self.h.sent),1)

    def test_manual_input_preserves_result_tracking_but_fences_further_actions(self):
        self.action(self.plan());self.brain.tick();self.h.now+=2
        self.brain.manual_send('one')
        self.h.messages['one'].append({'role':'user','text':'我直接補充要求'})
        self.completed('one','依補充完成');self.brain.tick()
        self.assertEqual(self.jobs()[0]['state'],'completed')
        self.assertIn('依補充完成',self.brain.state()['messages'][-1]['text'])
        self.assertTrue(self.jobs()[0]['manualObservation'])

    def test_shared_checkout_serializes_but_other_projects_parallel(self):
        self.h.sessions[1]['directory']=self.h.sessions[0]['directory']
        self.action(self.plan(('one','two')));self.brain.tick()
        self.assertEqual(len(self.h.sent),1)
        first=self.h.sent[0]['session'];self.completed(first,'第一件完成');self.brain.tick();self.brain.tick()
        self.assertEqual(len(self.h.sent),2)

    def test_same_session_new_task_queues_behind_exact_predecessor(self):
        self.action(self.plan());self.brain.tick();self.h.now+=1
        self.backend.turn['id']='next-user-turn'
        p=self.plan();p['topics'][0].update(id='new:second',goal='第二個授權目標')
        p['dispatches'][0].update(topic='new:second',instruction='接著第二件事')
        self.action(p,request='second');self.brain.tick();self.assertEqual(len(self.h.sent),1)
        self.completed('one','第一件');self.brain.tick();self.brain.tick()
        self.assertEqual(len(self.h.sent),2)
        self.assertNotEqual(self.jobs()[0]['topic'],self.jobs()[1]['topic'])

    def test_reused_binding_wrong_thread_blocks_result(self):
        self.action(self.plan());self.brain.tick()
        self.h.sessions[0]['codexThreadID']='unrelated';self.completed('one','不能採用')
        self.brain.tick();self.assertEqual(self.jobs()[0]['state'],'blocked')
        self.assertNotIn('不能採用',self.brain.state()['messages'][-1]['text'])

    def test_notify_canonical_result_and_read_receipt(self):
        self.backend.append('assistant','仍在執行',phase='commentary')
        self.assertFalse(any(s['id']=='brain' for s in self.brain.notification_state(self.h.state())['sessions']))
        self.backend.append('assistant','交付結果',phase='final_answer')
        self.assertTrue(self.brain.notification_state(self.h.state())['sessions'][-1]['unread'])
        self.brain.acknowledge({'through':2})
        self.assertFalse(self.brain.notification_state(self.h.state())['sessions'][-1]['unread'])

    def test_close_journal_no_history_delete_no_running_close(self):
        p={'topics':[],'dispatches':[],'launches':[],'release':[],'close':['one']}
        c=self.context();first=self.action(p,context=c);self.brain.tick();self.assertEqual(len(self.closed),1)
        self.assertEqual(self.action(p,context=c),first);self.assertTrue(self.h.messages['one'])
        self.backend.turn['id']='second-turn';self.h.sessions[1]['phase']='running'
        p['close']=['two']
        with self.assertRaises(ValueError):self.action(p,request='close-other')
        self.assertEqual(len(self.closed),1)


    def test_invalid_collection_types_are_controlled_and_atomic(self):
        for value in (None,{},[42]):
            p=self.plan();p['topics']=value
            with self.assertRaises(ValueError):self.action(p,request='bad'+str(value))
        p=self.plan();p['close']=[{}]
        with self.assertRaises(ValueError):self.action(p)
        self.assertFalse(self.jobs())

    def test_fresh_context_and_manual_steer_do_not_reuse_stale_authority(self):
        old=self.context();self.backend.turn['items'][0]['content'][0]['text']='新的交代'
        with self.assertRaisesRegex(ValueError,'補充'):self.action(self.plan(),context=old)
        new=self.context();self.assertNotEqual(old['contextID'],new['contextID'])
        self.action(self.plan(),context=new);self.assertEqual(len(self.jobs()),1)

    def test_cancel_one_queued_job_preserves_other_task_in_same_session(self):
        self.action(self.plan());self.brain.tick();self.h.now+=1
        self.backend.turn['id']='second';p=self.plan();p['topics'][0]['id']='new:second';p['dispatches'][0]['topic']='new:second';p['dispatches'][0]['instruction']='second task'
        self.action(p,request='second')
        jobs=self.jobs();self.backend.turn['id']='cancel'
        self.action({'topics':[],'dispatches':[],'launches':[],'release':[jobs[1]['id']]},request='cancel')
        result=self.jobs();self.assertEqual(result[1]['state'],'manual');self.assertEqual(result[0]['state'],jobs[0]['state'])

    def test_waiting_activity_read_without_chat_and_resume_removes_attention(self):
        self.action(self.plan());self.brain.tick();self.backend.fail=True
        self.h.sessions[0]['phase']='waiting';self.brain.tick()
        state=self.brain.notification_state(self.h.state());self.assertTrue(state['sessions'][-1]['unread'])
        activities=self.brain.state()['activities']
        self.brain.acknowledge({'through':0,'activities':[a['id'] for a in activities]})
        self.assertFalse(any(s.get('unread') for s in self.brain.notification_state(self.h.state())['sessions'] if s['id']=='brain'))
        self.h.sessions[0]['phase']='running';self.brain.tick()
        self.assertFalse(any(s.get('unread') for s in self.brain.notification_state(self.h.state())['sessions'] if s['id']=='brain'))

    def test_native_and_web_final_read_sync_matches_exact_notice_turn(self):
        self.backend.append('assistant','完成',turn='brain-final',phase='final_answer')
        native={'id':'brain','tool':'codex','lastTurn':'brain-final','noticeID':'native-notice','unread':False}
        self.h.sessions.append(native)
        self.assertFalse(self.brain.notification_state(self.h.state())['sessions'][-1]['unread'])
        calls=[];self.brain.ack_native=calls.append
        self.brain.acknowledge({'through':1,'noticeID':'old-notice'});self.assertFalse(calls)
        self.brain.acknowledge({'through':1,'noticeID':'native-notice'});self.assertEqual(calls,[{'session':'brain','noticeID':'native-notice'}])

    def test_crash_after_close_intent_reports_uncertainty_without_reclosing(self):
        p={'topics':[],'dispatches':[],'launches':[],'release':[],'close':['one']}
        self.action(p)
        with self.brain.lock,self.brain.db:
            record=self.brain._rows('closings')[0];record['state']='uncertain';self.brain._save('closings',record)
        self.brain.close();self.make();self.brain.tick()
        self.assertFalse(self.closed)
        self.assertIn('待確認',self.brain.state()['messages'][-1]['text'])
        self.assertEqual(self.backend.sources[-1]['dispatchBudget'],0)

    def test_task_index_read_uses_canonical_chat_not_old_parallel_messages(self):
        result=self.action(self.plan());topic=next(iter(result['topics'].values()))
        with self.brain.lock,self.brain.db:self.brain._message('old parallel error',kind='error',topic=topic)
        self.backend.append('assistant','原對話給本人的回覆',phase='final_answer')
        self.assertTrue(self.brain.state()['topics'][0]['unread'])
        self.brain.acknowledge({'through':1})
        self.assertFalse(self.brain.state()['topics'][0]['unread'])
        self.assertNotIn('old parallel error',self.brain.state()['topics'][0]['preview'])

if __name__=='__main__':unittest.main()
