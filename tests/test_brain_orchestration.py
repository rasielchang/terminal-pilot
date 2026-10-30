import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from brain_service import BrainService
from brain_targets import conversation_target
from brain_provider import plan_schema, validate_plan
from brain_context import prepare
from test_brain import Harness, plan


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.h = Harness(); self.opened = []
        for s in self.h.sessions: s['directory'] = '/project'
        self.archived = [conversation_target(s) for s in self.h.sessions]
        self.make()

    def make(self):
        self.brain = BrainService(self.temp.name, self.h.state, self.h.thread, self.h.send,
            planner=self.h.planner, launch=self.launch, targets=lambda: copy.deepcopy(self.archived), clock=lambda:self.h.now)

    def tearDown(self): self.brain.close(); self.temp.cleanup()

    def launch(self, data):
        self.opened.append(data)
        s = {'id':data['launchID'], 'binding':'new-tty', 'directory':data['directory'], 'tool':data['tool'],
             'title':data['name'], 'alive':True, 'canSend':True, 'phase':'complete', 'codexThreadID': data['sessionID'] or 'new-thread'}
        self.h.sessions.append(s); self.h.messages[s['id']] = [{'role':'user', 'text':'原對話最後交代'}]
        return {'opened':True, 'session':s['id'], 'binding':s['binding']}

    def stage(self, instruction='請繼續修正並驗證', target=None):
        target = target or self.archived[0]['id']
        value = {**plan('我會接回這個工作。'), 'topics':[{'id':'new:task','title':'修正手機','goal':'修正手機','state':'open','reply':''}],
                 'launches':[{'target':target, 'topic':'new:task', 'title':'手機修正', 'instruction':instruction}]}
        self.h.plans = [value]
        self.brain.submit({'requestID':'request','text':'接續原手機工程工作'})
        self.brain.tick()

    def jobs(self): return self.brain._rows('jobs')

    def test_closed_window_exact_resume_and_full_result_loop(self):
        self.h.sessions.pop(0); self.stage()
        self.brain.tick(); self.brain.tick()
        self.assertEqual(self.opened[0]['sessionID'], 'thread1')
        self.assertEqual(self.opened[0]['mode'], 'specific')
        self.assertEqual(len(self.h.sent), 1)
        job = self.jobs()[0]; session = job['session']
        self.assertEqual(job['target']['thread'], 'thread1')
        self.h.sessions[-1]['phase'] = 'complete'
        self.h.messages[session].append({'role':'assistant','phase':'final_answer','text':'修正已測，尚未部署。'})
        self.h.plans = [plan('工程回報已修正測試，尚未部署。')]; self.brain.tick()
        self.assertEqual(self.jobs()[0]['state'], 'completed')
        self.assertIn('尚未部署', self.brain.state(job['topic'])['messages'][-1]['text'])

    def test_same_project_multiple_conversations_are_not_interchangeable(self):
        other = {**self.h.sessions[0], 'id':'same-project', 'codexThreadID':'thread-other', 'binding':'tty-other'}
        self.h.sessions.append(other); self.h.messages[other['id']] = []
        self.h.sessions.pop(0); self.stage(); self.brain.tick(); self.brain.tick()
        self.assertEqual(len(self.opened), 1)
        self.assertNotEqual(self.h.sent[0]['session'], 'same-project')
        self.assertEqual(self.jobs()[0]['thread'], 'thread1')

    def test_live_precise_target_is_reused_and_busy_waits_without_interrupt(self):
        self.h.sessions[0]['phase'] = 'running'; self.stage(); self.brain.tick()
        self.assertFalse(self.opened); self.assertFalse(self.h.sent)
        self.assertEqual(self.jobs()[0]['state'], 'queued')
        self.h.sessions[0]['phase'] = 'waiting'; self.brain.tick(); self.assertFalse(self.h.sent)
        self.h.sessions[0]['phase'] = 'complete'; self.brain.tick(); self.assertEqual(len(self.h.sent), 1)

    def test_lost_launch_reply_restart_reconciles_only_original_identity(self):
        self.h.sessions.pop(0); self.stage()
        def lost(data): self.launch(data); raise OSError('lost reply')
        self.brain.launch_work = lost; self.brain.tick()
        self.brain.close(); self.make(); self.brain.tick(); self.brain.tick()
        self.assertEqual(len(self.opened), 1); self.assertEqual(len(self.h.sent), 1)

    def test_uncertain_launch_is_never_replayed_even_without_receipt(self):
        self.h.sessions.pop(0); self.stage()
        def lost(data): self.opened.append(data); raise OSError('lost')
        self.brain.launch_work = lost; self.brain.tick(); self.h.now += 100
        self.brain.close(); self.make()
        for _ in range(3): self.brain.tick()
        self.assertEqual(len(self.opened), 1); self.assertFalse(self.h.sent)
        self.assertEqual(self.brain.state()['openings'][0]['state'], 'uncertain')

    def test_changed_conversation_after_open_never_receives_instruction(self):
        self.h.sessions.pop(0); self.stage(); self.brain.tick()
        self.h.sessions[-1]['codexThreadID'] = 'wrong-thread'; self.brain.tick()
        self.assertFalse(self.h.sent); self.assertEqual(self.jobs()[0]['state'], 'blocked')

    def test_direct_open_is_idempotent_and_does_not_repeat_completed_job(self):
        self.stage(); self.brain.tick()
        job = self.jobs()[0]; self.h.sessions.pop(0)
        request = {'requestID':'open-original', 'job':job['id']}
        first = self.brain.open_work(request); self.assertEqual(self.brain.open_work(request), first)
        sent = len(self.h.sent); self.brain.tick(); self.brain.tick()
        self.assertEqual(len(self.opened), 1); self.assertEqual(len(self.h.sent), sent)
        result = next(o for o in self.brain.state()['openings'] if o['id'] == first['opening'])
        self.assertEqual(result['state'], 'ready'); self.assertEqual(result['session'], self.opened[0]['launchID'])

    def test_cancel_before_native_launch_does_not_open_and_manual_input_fences_delivery(self):
        self.h.sessions.pop(0); self.stage(); job = self.jobs()[0]
        self.h.plans = [plan('已取消後續交辦。', release=[job['id']])]
        self.h.now += 1
        self.brain.submit({'requestID':'cancel', 'text':'取消剛剛的工作'})
        self.brain.tick(); self.brain.tick()
        self.assertFalse(self.opened); self.assertFalse(self.h.sent)
        self.assertEqual(self.jobs()[0]['state'], 'manual')

    def test_native_terminal_input_between_plan_and_reuse_is_not_overwritten(self):
        self.stage()
        self.h.messages['one'].append({'role':'user','text':'原 Terminal 的新指令'})
        self.brain.tick(); self.assertFalse(self.h.sent)
        self.assertEqual(self.jobs()[0]['state'], 'manual')

    def test_native_input_after_archived_snapshot_fences_resume_dispatch(self):
        import hashlib
        self.brain.describe_target = lambda t:{'preview':'先前交代','baseline':hashlib.sha256('舊交代'.encode()).hexdigest()}
        self.h.sessions.pop(0); self.stage(); self.brain.tick(); self.brain.tick()
        self.assertFalse(self.h.sent); self.assertEqual(self.jobs()[0]['state'], 'manual')

    def test_manual_input_while_resume_connects_is_not_overwritten(self):
        self.h.sessions.pop(0); self.stage(); self.brain.tick()
        self.h.now += 1; self.brain.manual_send(self.h.sessions[-1]['id'])
        self.brain.tick(); self.assertFalse(self.h.sent)
        self.assertEqual(self.jobs()[0]['state'], 'manual')

    def test_late_startup_wait_has_visible_state_without_any_auto_approval(self):
        self.h.sessions.pop(0); self.stage(); self.brain.tick()
        self.h.sessions[-1].update(codexThreadID='', phase='waiting', runtime={'approvals':[{'id':'permission'}]})
        self.h.now += 100; self.brain.tick()
        state = self.brain.state()
        self.assertEqual(state['openings'][0]['state'], 'uncertain')
        self.assertIn('確認', state['jobs'][0]['detail'])
        self.assertTrue(state['decisions']); self.assertFalse(self.h.sent)
        self.assertEqual(len(self.opened), 1)

    def test_no_launch_capability_or_unknown_target_cannot_create_work(self):
        self.stage(target='invented')
        self.assertFalse(self.jobs()); self.assertFalse(self.opened)
        schema = plan_schema({'dispatchBudget':6, 'targets':[]})
        self.assertEqual(schema['properties']['launches']['maxItems'], 0)

    def test_new_project_keeps_project_and_work_names_separate(self):
        self.h.state = lambda:{'sessions':copy.deepcopy(self.h.sessions), 'projects':[
            {'id':'website', 'directory':'/project','tool':'codex','name':'網站專案'}]}
        self.brain.get_state = self.h.state
        self.stage(target='project:website'); self.brain.tick(); self.brain.tick()
        self.assertEqual(self.opened[0]['mode'], 'new'); self.assertEqual(self.opened[0]['name'], '手機修正')
        self.assertNotIn('projectID', self.opened[0]); self.assertEqual(self.jobs()[0]['thread'], 'new-thread')


class ContextTests(unittest.TestCase):
    def test_deltas_checkpoint_recovery_and_skill_mentions_are_data(self):
        source = {'requestID':'a', 'userGoal':'修正手機', 'trigger':{'kind':'user'}, 'scope':{'id':'topic'},
                  'jobs':[{'id':'job', 'state':'running'}], 'sessions':[{'id':'one','recent':[{'text':'參考 $radar $weekly $today'}]}],
                  'topics':[{'id':'topic','title':'手機'}], 'memory':[{'text':'$radar'}],
                  'conversation':[{'role':'assistant','text':'$weekly'}]}
        initial, cursor, checkpoint = prepare(source, None, 'policy', 'thread', '')
        delta, cursor2, checkpoint2 = prepare({**source,'requestID':'b'}, cursor, 'policy', 'thread', '')
        self.assertTrue(checkpoint); self.assertFalse(checkpoint2)
        self.assertNotIn('$radar', initial); self.assertNotIn('$weekly', initial); self.assertNotIn('$today', initial)
        self.assertIn('\\u0024radar', initial)
        self.assertNotIn('recent', delta); self.assertNotIn('memory', delta)
        self.assertLess(len(delta.encode()), len(initial.encode()))
        for thread, boundary, policy in [('new-thread','','policy'),('thread','compact-turn','policy'),('thread','','updated-policy')]:
            rebuilt, _, recovery = prepare(source, cursor2, policy, thread, boundary)
            self.assertTrue(recovery); self.assertIn('recent', rebuilt)
        explicit, _, _ = prepare({**source,'userGoal':'$today'}, cursor, 'policy', 'thread', '')
        self.assertIn('本輪使用者原文：\n$today', explicit)

    def test_removed_targets_and_changed_results_are_not_silently_stale(self):
        source = {'requestID':'a','targets':[{'id':'one'},{'id':'two'}], 'jobs':[{'id':'j','state':'running'}]}
        _, cursor, _ = prepare(source, None, 'policy', 'thread', '')
        text, _, _ = prepare({'requestID':'b','targets':[{'id':'two'}], 'jobs':[{'id':'j','state':'completed','result':'done'}]}, cursor, 'policy', 'thread', '')
        self.assertIn('"remove":["one"]', text); self.assertIn('"result":"done"', text)


if __name__ == '__main__': unittest.main()
