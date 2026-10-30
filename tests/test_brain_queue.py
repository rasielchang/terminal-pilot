"""Queue recovery/fairness at the official app-server boundary; no real work."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from brain_codex import CodexBrain,BrainPending

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.now=1000;self.queue=[];self.turns=[];self.calls=[];self.lost=False
        self.session={'id':'brain','binding':'binding','tool':'codex','codexThreadID':'thread','alive':True,'phase':'complete'}
        self.source={'natural':True,'requestID':'phone','userGoal':'本人交代','trigger':{'kind':'user'},'sessions':[],'topics':[],'jobs':[],'targets':[],'dispatchBudget':6,'releasableJobs':[]}
        self.make()
        self.backend.data.update(session='brain',binding='binding',thread='thread',requests={});self.backend._save()
    def make(self):self.backend=CodexBrain(self.temp.name,lambda:{'sessions':[copy.deepcopy(self.session)]},lambda *_:self.fail('No native launch'),self.rpc,clock=lambda:self.now)
    def tearDown(self):self.temp.cleanup()
    def rpc(self,session,method,p):
        self.calls.append((method,p))
        if method=='thread/queue/add':
            q={'id':'queue:'+p['clientUserMessageId'],'clientUserMessageId':p['clientUserMessageId'],'input':p['input']};self.queue.append(q)
            if self.lost:raise OSError('lost accepted reply')
            return {'queuedSubmission':q}
        if method=='thread/queue/list':return {'data':copy.deepcopy(self.queue)}
        if method=='thread/inject_items':return {}
        if method=='thread/turns/list':
            n=int(p.get('cursor',0));return {'data':copy.deepcopy(self.turns[n:n+1]),'nextCursor':str(n+1) if n+1<len(self.turns) else None}
        if method=='thread/queue/start':
            if self.session['phase']=='running':raise ValueError('already active')
            q=next(q for q in self.queue if q['id']==p['queuedSubmissionId']);self.queue.remove(q)
            turn={'id':'turn:'+q['clientUserMessageId'],'status':'completed','startedAt':self.now,'items':[{'type':'userMessage','clientId':q['clientUserMessageId'],'content':q['input']}]}
            self.turns.insert(0,turn)
            if self.lost:raise OSError('lost accepted start reply')
            return {'turn':turn}
        self.fail(method)
    def pending(self):
        with self.assertRaises(BrainPending):self.backend(self.source)
    def test_lost_add_and_start_replies_restart_do_not_duplicate(self):
        self.lost=True;self.pending();self.make();self.pending();self.make()
        self.assertTrue(self.backend(self.source)['completed'])
        self.assertEqual(sum(m=='thread/queue/add' for m,_ in self.calls),1)
        self.assertEqual(sum(m=='thread/queue/start' for m,_ in self.calls),1)
    def test_active_and_earlier_tui_input_are_not_steered_or_overtaken(self):
        self.queue=[{'id':'earlier','clientUserMessageId':'tui','input':[]}]
        self.session['phase']='running';self.pending();self.pending()
        self.session['phase']='complete';self.pending()
        self.assertFalse(any(m in {'thread/queue/start','turn/start','turn/steer','thread/inject_items'} for m,_ in self.calls))
        self.queue.pop(0);self.pending();self.assertTrue(self.backend(self.source)['completed'])
    def test_long_offline_recovery_paginates_across_ticks(self):
        self.pending();self.pending()
        self.turns[:0]=[{'id':str(i),'status':'completed','startedAt':1100,'items':[]} for i in range(40)]
        self.now=1200;self.make()
        for _ in range(5):self.pending()
        self.assertTrue(self.backend(self.source)['completed'])
        self.assertEqual(sum(m=='thread/queue/add' for m,_ in self.calls),1)
    def test_exact_manual_reopen_binding_and_ambiguous_thread(self):
        self.session.update(id='reopened',binding='new')
        self.assertTrue(self.backend.snapshot([self.session])['connected']);self.assertEqual(self.backend.session_id,'reopened')
        duplicate={**self.session,'id':'duplicate','binding':'other'}
        self.assertFalse(self.backend.snapshot([self.session,duplicate])['connected'])
        self.assertFalse(self.backend.snapshot([{**self.session,'codexThreadID':'wrong'}])['connected'])
    def test_context_size_delta_reconnect_and_no_repeated_skills(self):
        self.source['targets']=[{'id':str(i),'title':'project '+str(i),'preview':'x'*400} for i in range(20)]
        self.source['memory']=[{'text':'PRIVATE SKILL BODY'}];self.source['conversation']=[{'text':'OLD CHAT'}]
        self.pending();first=self.backend.data['requests']['phone']['contextBytes'];self.pending();self.backend(self.source)
        self.source['requestID']='second';self.pending();record=self.backend.data['requests']['second']
        self.assertLess(record['contextBytes'],first/3);self.assertNotIn('PRIVATE SKILL BODY',record['context']);self.assertNotIn('OLD CHAT',record['context'])
        self.pending();self.backend(self.source);self.session['binding']='reconnected'
        self.source['requestID']='third';self.pending();self.assertTrue(self.backend.data['requests']['third']['contextCheckpoint'])

if __name__=='__main__':unittest.main()
