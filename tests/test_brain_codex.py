import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from brain_codex import CodexBrain, BrainPending

PLAN = {'reply':'合成測試已完成。', 'release':[], 'dispatches':[]}
SOURCE = {'requestID':'turn-request', 'userGoal':'先整理', 'trigger':{'kind':'user'},
          'sessions':[], 'releasableJobs':[], 'dispatchBudget':6}

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.now = 1000
        self.sessions=[]; self.starts=[]; self.opens=[]; self.turns=[]; self.items={}; self.lost=False
        self.make()
    def tearDown(self): self.temp.cleanup()
    def make(self):
        self.backend=CodexBrain(self.temp.name, lambda:{'sessions':copy.deepcopy(self.sessions)}, self.native,self.rpc, clock=lambda:self.now)
    def native(self, method, params):
        self.assertEqual(method,'brainOpen'); self.opens.append(params)
        self.sessions=[{'id':params['launchID'], 'binding':'window-1', 'tool':'codex','alive':True,
                       'codexThreadID':params['threadID'] or 'thread-one','phase':'connected','runtime':{'state':'idle'}}]
        return {'opened':True, 'binding':'window-1', 'session':params['launchID']}
    def rpc(self, session, method, params):
        self.assertEqual(params['threadId'],session['codexThreadID'])
        if method=='thread/turns/list': return {'data':[{**t,'items':list(reversed(self.items.get(t['id'],[])))} for t in copy.deepcopy(self.turns)]}
        if method=='thread/items/list': return {'data':[{'item':i} for i in self.items.get(params['turnId'],[])]}
        self.assertEqual(method,'turn/start'); self.starts.append(params)
        self.turns=[{'id':'codex-turn','status':'inProgress','startedAt':self.now}]
        self.items['codex-turn']=[{'type':'userMessage','content':params['input']}]
        if self.lost: raise OSError('lost response')
        return {'turn':self.turns[0]}
    def start(self):
        self.backend.open()
        with self.assertRaises(BrainPending): self.backend(SOURCE)
    def complete(self):
        self.turns[0]['status']='completed'
        self.items['codex-turn'].insert(0,{'type':'agentMessage','phase':'final_answer','text':json.dumps(PLAN)})
    def test_unmaterialized_fresh_thread_can_receive_its_first_message(self):
        from codex_live import StatusRequestError
        rpc=self.backend.rpc
        def fresh(session,method,params):
            if method=='thread/turns/list' and not self.starts:
                raise StatusRequestError(-32600,'thread x is not materialized yet; unavailable before first user message')
            return rpc(session,method,params)
        self.backend.rpc=fresh
        self.start(); self.assertEqual(len(self.starts),1)
    def test_one_real_session_reused_and_exact_completed_result(self):
        self.start(); self.backend.open()
        self.assertEqual(len(self.opens),1)
        with self.assertRaises(BrainPending): self.backend(SOURCE)
        self.complete(); self.assertEqual(self.backend(SOURCE),PLAN)
        self.assertEqual(len(self.starts),1)
        for field in ('model','approvalPolicy','sandboxPolicy','approvalsReviewer'): self.assertNotIn(field,self.starts[0])
        self.assertIn('outputSchema',self.starts[0]); self.assertEqual(self.starts[0]['clientUserMessageId'],'turn-request')
    def test_restart_waits_original_turn_and_does_not_repeat_tools(self):
        self.start(); self.make()
        with self.assertRaises(BrainPending): self.backend(SOURCE)
        self.complete(); self.assertEqual(self.backend(SOURCE),PLAN)
        self.make(); self.assertEqual(self.backend(SOURCE),PLAN)
        self.assertEqual(len(self.starts),1)
    def test_lost_start_response_correlates_exact_marker(self):
        self.lost=True; self.start(); self.make()
        with self.assertRaises(BrainPending): self.backend(SOURCE)
        self.complete(); self.assertEqual(self.backend(SOURCE),PLAN); self.assertEqual(len(self.starts),1)
    def test_disconnection_while_waiting_recovers_without_restarting_turn(self):
        self.start(); rpc=self.backend.rpc
        self.backend.rpc=lambda *_: (_ for _ in ()).throw(OSError('connection lost'))
        with self.assertRaises(BrainPending): self.backend(SOURCE)
        self.backend.rpc=rpc; self.complete()
        self.assertEqual(self.backend(SOURCE),PLAN); self.assertEqual(len(self.starts),1)
    def test_unknown_delivery_never_resends(self):
        self.lost=True; self.start(); self.turns=[]; self.now+=50
        with self.assertRaisesRegex(ValueError,'沒有重送'): self.backend(SOURCE)
        self.make()
        with self.assertRaises(ValueError): self.backend(SOURCE)
        self.assertEqual(len(self.starts),1)
    def test_closed_terminal_resumes_only_saved_thread(self):
        self.start(); self.sessions=[]
        self.backend.open()
        self.assertEqual(self.opens[-1]['threadID'],'thread-one')
        self.assertNotEqual(self.opens[0]['launchID'],self.opens[-1]['launchID'])
        self.assertEqual(len(self.starts),1)
    def test_changed_binding_or_thread_cannot_retarget(self):
        self.start(); self.sessions[0]['codexThreadID']='other'
        with self.assertRaisesRegex(ValueError,'對話已變更'): self.backend(SOURCE)
        self.sessions[0]['codexThreadID']='thread-one'; self.sessions[0]['binding']='other'
        with self.assertRaisesRegex(ValueError,'綁定已變更'): self.backend(SOURCE)
        self.assertEqual(len(self.starts),1)
    def test_approval_wait_never_approves_or_fails_turn(self):
        self.start(); self.sessions[0].update(phase='waiting', runtime={'state':'approval'})
        with self.assertRaises(BrainPending): self.backend(SOURCE)
        self.assertEqual(len(self.starts),1)
        self.complete(); self.assertEqual(self.backend(SOURCE),PLAN)
    def test_interrupted_or_non_schema_output_cannot_dispatch(self):
        self.start(); self.turns[0]['status']='interrupted'
        with self.assertRaisesRegex(ValueError,'中斷'): self.backend(SOURCE)
    def test_open_without_inference_and_recovery_after_lost_launch_response(self):
        def native(method,params): self.native(method,params); raise OSError('lost')
        self.backend.native=native
        with self.assertRaises(OSError): self.backend.open()
        self.make(); self.assertTrue(self.backend.open()['opened'])
        self.assertEqual(len(self.opens),1); self.assertFalse(self.starts)
    def test_snapshot_saves_thread_before_window_is_closed(self):
        self.backend.open(); self.backend.snapshot(self.sessions); self.sessions=[]
        self.make(); self.backend.open(); self.assertEqual(self.opens[-1]['threadID'],'thread-one')

if __name__=='__main__': unittest.main()
