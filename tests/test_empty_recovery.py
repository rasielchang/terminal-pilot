import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from empty_recovery import empty_source
from test_brain_unified import UnifiedTests
from brain_targets import conversation_target

class SourceTests(unittest.TestCase):
    def test_only_pristine_ended_exact_source_can_recover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=root/'store';home=root/'claude';(store/'sessions').mkdir(parents=True);(home/'projects/project').mkdir(parents=True)
            thread=str(uuid.uuid4());session=str(uuid.uuid4());transcript=home/'projects/project'/(thread+'.jsonl')
            target={'tool':'claude','mode':'specific','thread':thread,'directory':'/project','agentHome':str(home)}
            data={'id':session,'tool':'claude','phase':'ended','eventSequence':3,'noticeAt':0,'noticeID':'','claudeHome':str(home),'claudeSessionID':thread,'cwd':'/project','claudeTranscript':str(transcript)}
            path=store/'sessions'/(session+'.json');path.write_text(json.dumps(data))
            self.assertEqual(empty_source(store,target)['session'],session)
            for change in ({'phase':'running'},{'eventSequence':4},{'noticeAt':1},{'cwd':'/other'},{'claudeHome':'/other'}):
                path.write_text(json.dumps({**data,**change}));self.assertIsNone(empty_source(store,target))
            path.write_text(json.dumps(data));transcript.write_text('malformed')
            self.assertIsNone(empty_source(store,target))
            transcript.unlink();transcript.symlink_to(root/'missing');self.assertIsNone(empty_source(store,target))

class RecoveryTests(UnifiedTests):
    def setup_empty(self):
        self.h.sessions.pop(0)
        old=self.targets[0];target={**old,'tool':'claude'};self.targets[0]=target
        topic='fixture:one';empty=hashlib.sha256(b'').hexdigest()
        self.proof={'session':'old-session','evidence':'exact'}
        self.brain.describe_target=lambda _: {'preview':'','baseline':None,'emptyRecovery':self.proof}
        self.brain.closure_status=lambda r:{'reliable':True,'absent':True,'generation':r['generation']}
        with self.brain.lock,self.brain.db:
            self.brain._save('jobs',{'id':'old-job','session':'old-session','topic':topic,'target':target,'state':'manual','baseline':empty,'binding':'old-binding','generation':'old-generation','created':1,'goal':0,'instruction':'old','thread':target['thread'],'title':'old'})
            self.brain._save('openings',{'id':'old-session','job':'old-job','session':'old-session','target':{'mode':'new','id':'old-workspace'},'state':'ready','topic':topic})
        return {'topics':[],'dispatches':[],'release':[],'launches':[{'target':target['id'],'topic':topic,'title':'recover','instruction':'same authorized task'}]}
    def test_recovery_preserves_identity_and_idempotency(self):
        plan=self.setup_empty();c=self.context();r=self.action(plan,context=c)
        self.assertEqual(self.action(plan,context=c),r)
        self.brain.tick();self.assertEqual(len(self.opened),1)
        self.assertEqual(self.opened[0]['sessionID'],self.targets[0]['thread'])
        self.assertEqual(self.opened[0]['mode'],'specific');self.assertEqual(self.opened[0]['emptyRecoverySession'],'old-session')
        self.h.messages[self.opened[0]['launchID']]=[]
        self.brain.tick();self.brain.tick()
        self.assertEqual(len(self.h.sent),1)
        self.assertEqual(self.jobs()[-1]['thread'],self.targets[0]['thread'])
    def test_prior_send_or_unconfirmed_closure_never_recreates(self):
        plan=self.setup_empty()
        self.brain.closure_status=lambda _:{'reliable':False}
        with self.assertRaises(ValueError):self.action(plan)
        self.brain.closure_status=lambda r:{'reliable':True,'absent':True,'generation':r['generation']}
        with self.brain.lock,self.brain.db:
            job=self.jobs()[0];job['sentAt']=1;self.brain._save('jobs',job)
        with self.assertRaises(ValueError):self.action(plan)
        self.assertFalse(self.opened)
    def test_changed_source_between_apply_and_open_blocks(self):
        plan=self.setup_empty();self.action(plan);self.proof={'session':'old-session','evidence':'changed'}
        self.brain.tick();self.assertFalse(self.opened)
        self.assertEqual(self.jobs()[-1]['state'],'blocked')

def load_tests(loader, tests, pattern):
    suite=unittest.TestSuite(loader.loadTestsFromTestCase(SourceTests))
    for name in ('test_recovery_preserves_identity_and_idempotency','test_prior_send_or_unconfirmed_closure_never_recreates','test_changed_source_between_apply_and_open_blocks'):suite.addTest(RecoveryTests(name))
    return suite
