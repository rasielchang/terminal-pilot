import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from brain_service import BrainService, read_memory
from brain_provider import validate_plan


def plan(reply="我會請網站工作查明原因。", *actions, release=()):
    return {"reply": reply, "dispatches": [{"session": s, "instruction": text} for s, text in actions], "release": list(release)}


class Harness:
    def __init__(self):
        self.sessions = [{"id": "one", "title": "網站", "tool": "codex", "binding": "tty1", "codexThreadID": "thread1",
                          "canSend": True, "alive": True, "phase": "complete", "updatedAt": 10},
                         {"id": "two", "title": "API", "tool": "claude", "binding": "tty2", "claudeSessionID": "thread2",
                          "canSend": True, "alive": True, "phase": "complete", "updatedAt": 8}]
        self.messages = {"one": [{"role": "user", "text": "上次的交代"}], "two": []}
        self.sent, self.prompts, self.plans = [], [], []
        self.fail_send = False
        self.now = 1000

    def state(self): return {"sessions": copy.deepcopy(self.sessions)}
    def thread(self, session):
        s = next(s for s in self.sessions if s["id"] == session)
        return {"session": copy.deepcopy(s), "messages": copy.deepcopy(self.messages[session])}
    def send(self, value):
        self.sent.append(value)
        if self.fail_send: raise OSError("connection lost")
        self.messages[value["session"]].append({"role": "user", "text": value["text"]})
        next(s for s in self.sessions if s["id"] == value["session"])["phase"] = "running"
        return {"sent": True}
    def planner(self, value):
        self.prompts.append(value)
        result = self.plans.pop(0)
        if isinstance(result, Exception): raise result
        return result


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.h = Harness()
        self.make()
    def make(self):
        self.brain = BrainService(self.temp.name, self.h.state, self.h.thread, self.h.send,
                                  planner=self.h.planner, memory_root=Path(self.temp.name) / "memory", clock=lambda: self.h.now)
    def tearDown(self):
        self.brain.close(); self.temp.cleanup()
    def submit(self, text="請修正網站問題", identity="user-1"):
        return self.brain.submit({"text": text, "requestID": identity})
    def jobs(self): return self.brain.state()["jobs"]
    def dispatch(self, session="one"):
        self.h.plans = [plan("我會處理。", (session, "查明問題並測試"))]
        self.submit(); self.brain.tick(); self.brain.tick()

    def test_full_loop_with_cross_session_followup_and_consolidated_report(self):
        self.h.plans = [plan("我會先查網站，再讓 API 工作核對。", ("one", "查明問題")),
                        plan("網站回報 API 格式不符，我會請 API 工作核對。", ("two", "核對 API 格式與網站提供的欄位")),
                        plan("兩邊已回報修正並測試；尚未部署。")]
        self.submit(); self.brain.tick(); self.brain.tick()
        self.assertEqual([x["session"] for x in self.h.sent], ["one"])
        self.h.messages["one"].append({"role": "assistant", "phase": "commentary", "text": "仍在查"})
        self.brain.tick(); self.assertEqual(len(self.h.prompts), 1)
        self.h.messages["one"].append({"role": "assistant", "phase": "final_answer", "text": "查到 API 格式不符。"})
        self.h.sessions[0].update(phase="complete", noticeID="n1", unread=True)
        self.brain.tick(); self.brain.tick()
        self.assertEqual([x["session"] for x in self.h.sent], ["one", "two"])
        self.assertEqual(self.h.prompts[1]["trigger"]["result"], "查到 API 格式不符。")
        self.h.messages["two"].append({"role": "assistant", "text": "API 與網站修正測試完成，尚未部署。"})
        self.h.sessions[1].update(phase="complete", noticeID="claude-result"); self.brain.tick()
        self.assertEqual([j["state"] for j in self.jobs()], ["completed", "completed"])
        self.assertIn("尚未部署", self.brain.state()["messages"][-1]["text"])
        self.assertFalse(self.brain.state()["working"])

    def test_restart_idempotency_and_conflicting_request(self):
        first = self.submit(); self.brain.close(); self.make()
        self.assertEqual(self.submit(), first)
        self.assertEqual(len(self.brain.state()["messages"]), 1)
        with self.assertRaises(ValueError): self.submit("其他指令")
        if os.name != 'nt':
            self.assertEqual(os.stat(Path(self.temp.name) / "brain.sqlite3").st_mode & 0o777, 0o600)

    def test_ambiguous_send_never_retried_even_after_restart(self):
        self.h.fail_send = True; self.dispatch()
        self.assertEqual(self.jobs()[0]["state"], "uncertain")
        self.brain.close(); self.make()
        self.brain.tick(); self.brain.tick()
        self.assertEqual(len(self.h.sent), 1)
        # A late receipt safely reconciles the original delivery.
        self.h.messages["one"].append({"role": "user", "text": self.h.sent[0]["text"]})
        self.h.sessions[0]["phase"] = "running"; self.brain.tick()
        self.assertEqual(self.jobs()[0]["state"], "running")

    def test_crash_between_journal_and_send_remains_uncertain(self):
        self.h.plans = [plan("處理", ("one", "查明"))]; self.submit(); self.brain.tick()
        with self.brain.lock, self.brain.db:
            job = self.brain._rows("jobs")[0]; job["state"] = "dispatching"; self.brain._save("jobs", job)
        self.brain.close(); self.make(); self.brain.tick()
        self.assertEqual(self.jobs()[0]["state"], "uncertain"); self.assertFalse(self.h.sent)

    def test_busy_queue_waits_and_manual_terminal_input_releases_ownership(self):
        self.h.sessions[0]["phase"] = "running"; self.dispatch()
        self.assertFalse(self.h.sent); self.assertEqual(self.jobs()[0]["state"], "queued")
        self.h.messages["one"].append({"role": "user", "text": "我改成手動處理"})
        self.h.sessions[0]["phase"] = "complete"; self.brain.tick()
        self.assertEqual(self.jobs()[0]["state"], "manual"); self.assertFalse(self.h.sent)

    def test_busy_queue_sends_after_same_turn_finishes(self):
        self.h.sessions[0]["phase"] = "running"; self.dispatch(); self.assertFalse(self.h.sent)
        self.h.messages["one"].append({"role": "assistant", "phase": "final_answer", "text": "上一件已完成"})
        self.h.sessions[0]["phase"] = "complete"; self.brain.tick()
        self.assertEqual(len(self.h.sent), 1)

    def test_pause_is_a_dispatch_fence_but_does_not_stop_agents(self):
        self.h.plans = [plan("處理", ("one", "查明"))]; self.submit(); self.brain.tick()
        self.brain.control({"requestID": "pause", "action": "pause"}); self.brain.tick()
        self.assertFalse(self.h.sent)
        self.brain.control({"requestID": "resume", "action": "resume"}); self.brain.tick()
        self.assertEqual(len(self.h.sent), 1)

    def test_rebound_thread_or_terminal_is_not_retargeted(self):
        self.h.plans = [plan("處理", ("one", "查明"))]; self.submit(); self.brain.tick()
        self.h.sessions[0]["codexThreadID"] = "new-thread"; self.brain.tick()
        self.assertEqual(self.jobs()[0]["state"], "blocked"); self.assertFalse(self.h.sent)

    def test_closed_session_does_not_send_or_launch(self):
        self.h.plans = [plan("處理", ("one", "查明"))]; self.submit(); self.brain.tick()
        self.h.sessions[0]["alive"] = False; self.brain.tick()
        self.assertEqual(self.jobs()[0]["state"], "blocked"); self.assertFalse(self.h.sent)

    def test_manual_reply_does_not_become_result_or_autonomous_authority(self):
        self.dispatch()
        self.h.messages["one"] += [{"role": "assistant", "text": "舊結果"}, {"role": "user", "text": "新的手動交代"}, {"role": "assistant", "text": "新結果"}]
        self.h.sessions[0]["phase"] = "complete"; self.brain.tick()
        self.assertEqual(self.jobs()[0]["state"], "manual"); self.assertEqual(len(self.h.prompts), 1)

    def test_duplicate_cli_user_events_are_one_receipt_not_manual_takeover(self):
        self.dispatch()
        self.h.messages['one'].append({'role':'user','text':self.h.sent[0]['text']})
        self.h.messages['one'].append({'role':'assistant','text':'已完成'})
        self.h.sessions[0]['phase'] = 'complete'; self.h.plans = [plan('已收到完成回報。')]
        self.brain.tick()
        self.assertEqual(self.jobs()[0]['state'], 'completed')
        self.assertEqual(len(self.h.sent), 1)

    def test_claude_partial_text_needs_a_new_formal_completion_notice(self):
        self.h.sessions[1]['noticeID'] = 'old-result'; self.dispatch('two')
        self.h.messages['two'].append({'role':'assistant','text':'我正在做第一步'})
        self.h.sessions[1]['phase'] = 'complete'; self.brain.tick()
        self.assertEqual(self.jobs()[0]['state'], 'observing')
        self.assertEqual(len(self.h.prompts), 1)
        self.h.messages['two'].append({'role':'assistant','text':'已完成'})
        self.h.sessions[1]['noticeID'] = 'new-result'; self.h.plans = [plan('收到完成結果。')]
        self.brain.tick(); self.assertEqual(self.jobs()[0]['state'], 'completed')

    def test_approval_is_visible_but_never_sent_as_text_or_auto_accepted(self):
        self.dispatch()
        self.h.sessions[0].update(phase="waiting", approvalReviewer="user", noticeID="approval1", runtime={"approvals": [{"id": "a", "actions": ["accept", "decline"]}]})
        self.brain.tick(); self.brain.tick()
        self.assertEqual(self.jobs()[0]["state"], "waiting")
        state = self.brain.state()
        self.assertEqual(state["decisions"][0]["requests"][0]["id"], "a")
        self.assertEqual(sum(m["kind"] == "question" for m in state["messages"]), 1)
        self.assertEqual(len(self.h.sent), 1)

    def test_no_observation_side_effects_without_user_goal(self):
        self.brain.tick(); self.brain.state()
        self.assertFalse(self.h.sent); self.assertFalse(self.h.prompts)

    def test_unowned_manual_approval_does_not_get_duplicate_brain_push(self):
        self.submit('只整理'); self.h.plans = [plan('這是目前狀況。')]; self.brain.tick()
        self.h.sessions[1].update(phase='waiting', approvalReviewer='user', noticeID='manual-approval', unread=True)
        self.brain.tick()
        self.assertFalse(any(m['kind'] == 'question' for m in self.brain.state()['messages']))
        self.assertEqual(len(self.brain.state()['decisions']), 1)

    def test_invalid_batch_is_atomic_and_provider_failure_visible(self):
        self.h.plans = [plan("做", ("one", "查"), ("invented", "也做"))]
        self.submit(); self.brain.tick()
        self.assertFalse(self.jobs()); self.assertFalse(self.h.sent)
        self.assertEqual(self.brain.state()["messages"][-1]["kind"], "error")
        self.h.plans = [ValueError("HTTP 429")]; self.submit("再試", "user-2"); self.brain.tick()
        self.assertIn("429", self.brain.state()["messages"][-1]["text"])

    def test_manual_takeover_during_model_call_invalidates_dispatch(self):
        self.dispatch(); self.h.sessions[0]["phase"] = "complete"
        self.h.messages["one"].append({"role": "assistant", "text": "還有下一步"})
        def planner(source):
            self.h.now += 1; self.brain.manual_send("one")
            return plan("繼續", ("one", "下一步"))
        self.brain.planner = planner; self.brain.tick()
        self.assertEqual(len(self.jobs()), 1); self.assertEqual(len(self.h.sent), 1)

    def test_goal_queued_before_manual_takeover_cannot_reclaim_session_later(self):
        self.dispatch()
        self.h.now += 1; self.submit('做下一步', 'user-2')
        self.h.now += 1; self.brain.manual_send('one')
        self.h.now += 1; self.h.plans = [plan('繼續', ('one','新的步驟'))]
        self.brain.tick()
        self.assertEqual(len(self.jobs()), 1)
        self.assertEqual(len(self.h.sent), 1)

    def test_user_can_release_queued_job_in_conversation(self):
        self.h.sessions[0]["phase"] = "running"; self.dispatch(); job = self.jobs()[0]
        self.h.sessions[0]["phase"] = "complete"  # User cancellation must win over a just-idle worker.
        self.h.plans = [plan("已停止後續派送。", release=[job["id"]])]
        self.submit("網站先不要做", "user-2"); self.brain.tick()
        self.assertEqual(self.jobs()[0]["state"], "manual"); self.assertFalse(self.h.sent)

    def test_result_plan_failure_preserves_completed_agent_report(self):
        self.dispatch()
        self.h.messages['one'].append({'role':'assistant','phase':'final_answer','text':'實際結果仍在這裡'})
        self.h.sessions[0]['phase']='complete'
        self.h.plans=[plan('完成', release=[self.jobs()[0]['id']])]
        self.brain.tick()
        messages=self.brain.state()['messages']
        self.assertEqual(messages[-1]['kind'],'error')
        self.assertEqual(messages[-2]['kind'],'result')
        self.assertIn('實際結果仍在這裡', messages[-2]['text'])
        self.assertEqual(self.jobs()[0]['state'],'completed')
        self.assertEqual(len(self.h.sent),1)

    def test_codex_pending_resumes_same_context_without_duplicate_dispatch(self):
        from brain_codex import BrainPending
        class Backend:
            session_id='coordinator'
            def snapshot(self, sessions): return {'kind':'codex'}
        self.brain.backend=Backend()
        self.h.sessions.append({'id':'coordinator','title':'Brain','tool':'codex','binding':'brain-tty','alive':True})
        self.h.plans=[BrainPending(), plan('我會處理。',('one','核對'))]
        self.submit(); self.brain.tick()
        self.assertTrue(self.brain.state()['working'])
        self.assertEqual(self.brain._rows('turns')[0]['state'],'codex_wait')
        self.assertFalse(any(s['id']=='coordinator' for s in self.h.prompts[0]['sessions']))
        self.h.messages['one'].append({'role':'assistant','text':'更新'})
        self.brain.tick()
        self.assertEqual(self.h.prompts[0],self.h.prompts[1])
        self.assertEqual(len(self.jobs()),1)
        self.brain.tick(); self.assertEqual(len(self.h.sent),1)

    def test_large_chinese_context_is_bounded_without_losing_goal(self):
        self.h.messages['one'] = [{'role':'user','text':'舊' * 8000},{'role':'assistant','text':'文' * 20000}]
        for i in range(7): self.submit('這' * 8000, 'many-' + str(i))
        with self.brain.lock:
            turn = self.brain._rows('turns')[0]
        source = self.brain._context(turn, self.h.state())
        self.assertEqual(source['userGoal'], '這' * 8000)
        self.assertLessEqual(len(json.dumps(source, ensure_ascii=False).encode()), 210000)

    def test_notifications_do_not_mutate_manager_or_suppress_future_manual_results(self):
        self.dispatch(); self.h.sessions[0].update(unread=True, noticeID="first", phase="complete")
        self.h.messages["one"].append({"role": "assistant", "text": "完成"}); self.h.plans = [plan("完成，尚未部署。")]; self.brain.tick()
        raw = self.h.state(); result = self.brain.notification_state(raw)
        self.assertTrue(raw["sessions"][0]["unread"]); self.assertFalse(result["sessions"][0]["unread"])
        self.assertEqual(result["sessions"][-1]["id"], "brain")
        self.h.sessions[0]["noticeID"] = "manual-next"
        self.assertTrue(self.brain.notification_state(self.h.state())["sessions"][0]["unread"])
        last = self.brain.state()["messages"][-1]["id"]
        self.brain.acknowledge({"through": last})
        self.assertFalse(self.brain.notification_state(self.h.state())["sessions"][-1]["unread"])

    def test_memory_is_bounded_and_never_follows_symlinks(self):
        root = Path(self.temp.name) / "memory"; root.mkdir()
        (root / "reminders.md").write_text("x" * 50000)
        try: (root / "projects").symlink_to(Path(self.temp.name), target_is_directory=True)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314: self.skipTest('Windows account lacks symbolic-link privilege')
            raise
        (Path(self.temp.name) / "secret.md").write_text("SECRET")
        (root / "AGENTS.md").symlink_to(Path(self.temp.name) / "secret.md")
        result = read_memory(root)
        self.assertEqual(len(result), 1); self.assertTrue(result[0]["partial"])
        self.assertNotIn("SECRET", json.dumps(result)); self.assertLessEqual(len(result[0]["text"]), 4000)

    def test_rejects_terminal_control_characters_and_extra_model_tools(self):
        with self.assertRaises(ValueError): self.submit("text\x1b[31m")
        with self.assertRaises(ValueError): validate_plan({**plan(), "shell": "rm"})
        with self.assertRaises(ValueError): validate_plan(plan("x", ("one", "\x1b")))


if __name__ == "__main__": unittest.main()
