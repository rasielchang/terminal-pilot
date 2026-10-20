import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/bridge.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="terminal-pilot-test-")
        self.store = Path(self.temp.name)
        self.session = str(uuid.uuid4())
        bridge.update(self.store, self.session, "connected", tty="/dev/ttysTEST", tool="claude",
                      label="Synthetic", cwd=str(self.store), pid=os.getpid())

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        return json.loads(bridge.session_path(self.store, self.session).read_text())

    def test_runtime_wait_notice_is_stable_clears_on_resume_and_rejects_stale_identity(self):
        thread = str(uuid.uuid4())
        bridge.update(self.store, self.session, "complete", tool="codex", codexThreadID=thread, approvalReviewer="auto_review")
        def snapshot():
            s = self.state()
            return {**s, "agentPID": s["pid"]}
        stale = snapshot()
        bridge.observe_codex_runtime(self.store, stale, "approval")
        self.assertEqual(self.state()["phase"], "waiting")
        self.assertEqual(self.state()["approvalReviewer"], "user")
        notice = self.state()["noticeID"]
        bridge.observe_codex_runtime(self.store, snapshot(), "approval")
        self.assertEqual(self.state()["noticeID"], notice)
        bridge.observe_codex_runtime(self.store, stale, "running")
        self.assertEqual(self.state()["noticeID"], notice)
        for change in ({"agentPID": -1}, {"codexThreadID": "other"}):
            bridge.observe_codex_runtime(self.store, {**snapshot(), **change}, "running")
            self.assertEqual(self.state()["noticeID"], notice)
        bridge.observe_codex_runtime(self.store, snapshot(), "running")
        self.assertEqual(self.state()["noticeID"], "")
        self.assertEqual(self.state()["phase"], "running")
        bridge.observe_codex_runtime(self.store, snapshot(), "question")
        self.assertEqual(self.state()["noticeReason"], "user_input")
        bridge.observe_codex_runtime(self.store, snapshot(), "idle")
        self.assertEqual(self.state()["noticeID"], "")
        bridge.update(self.store, self.session, "complete")
        before = self.state()
        for state in ("idle", "notLoaded", "unavailable", "error"):
            bridge.observe_codex_runtime(self.store, snapshot(), state)
            self.assertEqual(self.state(), before)

    def test_codex_thread_metadata_is_exact_and_contains_no_message(self):
        thread = str(uuid.uuid4())
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "UserPromptSubmit", "session_id": thread, "prompt": "PRIVATE"})
        self.assertEqual(self.state()["codexThreadID"], thread)
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "thread-id": thread, "turn-id": "t", "last-assistant-message": "PRIVATE"})
        self.assertEqual(self.state()["codexThreadID"], thread)
        self.assertNotIn("PRIVATE", json.dumps(self.state()))
        self.assertEqual(bridge.thread_metadata("../../outside"), {})

    def test_new_pending_request_notifies_but_replay_and_partial_resolution_do_not(self):
        bridge.update(self.store, self.session, 'running', tool='codex', codexThreadID='thread')
        def observe(keys):
            s = self.state()
            bridge.observe_codex_runtime(self.store, {**s, 'agentPID':s['pid'], 'pendingApprovalKeys':keys}, 'approval')
            return self.state()['noticeID']
        first = observe(['a'])
        self.assertEqual(observe(['a']), first)
        second = observe(['a','b'])
        self.assertNotEqual(first, second)
        self.assertEqual(observe(['b']), second)
        self.assertEqual(observe(['a','b']), second)

    def test_codex_child_notify_cannot_replace_primary_thread_or_notice(self):
        parent, child = str(uuid.uuid4()), str(uuid.uuid4())
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "UserPromptSubmit", "session_id": parent, "turn_id": "main-turn"})
        before = self.state()
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "thread-id": child, "turn-id": "child-turn"})
        self.assertEqual(self.state(), before)
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "thread-id": parent, "turn-id": "main-turn"})
        completed = self.state()
        self.assertEqual(completed["phase"], "complete")
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "thread-id": child, "turn-id": "another-child-turn"})
        self.assertEqual(self.state(), completed)
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "SessionStart", "session_id": str(uuid.uuid4())})
        self.assertNotEqual(self.state()["codexThreadID"], parent)

    def test_claude_turn_and_permission_lifecycle(self):
        bridge.on_hook(self.store, self.session, {"hook_event_name": "UserPromptSubmit", "prompt": "PRIVATE"})
        self.assertEqual(self.state()["phase"], "running")
        bridge.on_hook(self.store, self.session, {"hook_event_name": "PermissionRequest"})
        self.assertEqual(self.state()["phase"], "waiting")
        self.assertTrue(self.state()["noticeID"])
        bridge.on_hook(self.store, self.session, {"hook_event_name": "PostToolUse"})
        self.assertEqual(self.state()["noticeID"], "")
        bridge.on_hook(self.store, self.session, {"hook_event_name": "Stop"})
        self.assertEqual(self.state()["phase"], "complete")
        self.assertNotIn("PRIVATE", json.dumps(self.state()))

    def test_claude_metadata_questions_and_idle_do_not_renotify(self):
        sid = str(uuid.uuid4())
        data = {"session_id": sid, "transcript_path": str(self.store / (sid + ".jsonl"))}
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "SessionStart", "prompt": "PRIVATE"})
        self.assertEqual(self.state()["claudeSessionID"], sid)
        self.assertEqual(self.state()["claudeTranscript"], str(Path(data["transcript_path"]).resolve()))
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "Stop"})
        before = self.state()
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "Notification", "notification_type": "idle_prompt"})
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "Stop", "agent_id": "child", "session_id": str(uuid.uuid4())})
        self.assertEqual(self.state(), before)
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "PermissionRequest", "tool_name": "Bash"})
        self.assertEqual(self.state()["approvalReviewer"], "")
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "Notification", "notification_type": "permission_prompt"})
        self.assertEqual(self.state()["approvalReviewer"], "user")
        self.assertEqual(self.state()["waitingTool"], "Bash")
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "PostToolUse", "tool_name": "Bash"})
        self.assertEqual(self.state()["noticeID"], "")
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "Notification", "notification_type": "elicitation_dialog"})
        self.assertEqual(self.state()["noticeReason"], "user_input")
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "PostToolUse", "tool_name": "MCP"})
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"})
        self.assertEqual(self.state()["phase"], "waiting")
        self.assertEqual(self.state()["noticeReason"], "user_input")
        notice = self.state()["noticeID"]
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "PostToolUse", "tool_name": "Bash"})
        self.assertEqual(self.state()["noticeID"], notice)
        bridge.on_hook(self.store, self.session, {**data, "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion"})
        self.assertEqual(self.state()["noticeID"], "")
        self.assertNotIn("PRIVATE", json.dumps(self.state()))

    def test_standalone_permission_notification_does_not_reuse_old_tool(self):
        bridge.on_hook(self.store, self.session, {"hook_event_name": "PermissionRequest", "tool_name": "OldTool"})
        bridge.on_hook(self.store, self.session, {"hook_event_name": "PostToolUse", "tool_name": "OldTool"})
        bridge.on_hook(self.store, self.session, {"hook_event_name": "Notification", "notification_type": "permission_prompt"})
        self.assertIsNone(self.state()["waitingTool"])
        self.assertEqual(self.state()["approvalReviewer"], "user")

    def test_exit_does_not_erase_unread_completion(self):
        bridge.on_hook(self.store, self.session, {"hook_event_name": "Stop"})
        notice = self.state()["noticeID"]
        bridge.on_hook(self.store, self.session, {"hook_event_name": "SessionEnd"})
        self.assertEqual(self.state()["phase"], "ended")
        self.assertEqual(self.state()["noticeID"], notice)
        self.assertEqual(self.state()["noticePhase"], "complete")

    def test_new_prompt_resets_previous_notice(self):
        bridge.on_hook(self.store, self.session, {"hook_event_name": "Stop"})
        bridge.on_hook(self.store, self.session, {"hook_event_name": "UserPromptSubmit"})
        self.assertEqual(self.state()["noticeID"], "")

    def test_background_progress_preserves_completion_and_error_notices(self):
        for hook in ["Stop", "StopFailure"]:
            with self.subTest(hook=hook):
                bridge.on_hook(self.store, self.session, {"hook_event_name": hook})
                notice = self.state()["noticeID"]
                bridge.on_hook(self.store, self.session, {"hook_event_name": "PostToolUse"})
                self.assertEqual(self.state()["phase"], "running")
                self.assertEqual(self.state()["noticeID"], notice)
                bridge.update(self.store, self.session, "running")
                self.assertEqual(self.state()["noticeID"], notice)
                bridge.on_hook(self.store, self.session, {"hook_event_name": "UserPromptSubmit"})
                self.assertEqual(self.state()["noticeID"], "")

    def test_waiting_notice_only_resolves_on_input_or_tool_resume(self):
        for hook in ["PostToolUse", "UserPromptSubmit"]:
            with self.subTest(hook=hook):
                bridge.on_hook(self.store, self.session, {"hook_event_name": "PermissionRequest"})
                notice = self.state()["noticeID"]
                bridge.update(self.store, self.session, "running")
                self.assertEqual(self.state()["noticeID"], notice)
                bridge.on_hook(self.store, self.session, {"hook_event_name": hook})
                self.assertEqual(self.state()["noticeID"], "")

    def test_codex_completion_is_not_process_exit_or_arbitrary_output(self):
        bridge.on_notify(self.store, self.session, {"type": "progress"})
        self.assertFalse(self.state()["noticeID"])
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "turn-id": "t1", "last-assistant-message": "SECRET"})
        first = self.state()["noticeID"]
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "turn-id": "t1"})
        self.assertEqual(first, self.state()["noticeID"])
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "turn-id": "t2"})
        self.assertNotEqual(first, self.state()["noticeID"])
        self.assertNotIn("SECRET", json.dumps(self.state()))

    def test_codex_lifecycle_clears_attention_when_work_resumes(self):
        def hook(name, turn="t1", **data):
            bridge.on_codex_hook(self.store, self.session, {"hook_event_name": name, "turn_id": turn, **data})
        hook("SessionStart", source="startup")
        self.assertEqual(self.state()["lifecycle"], "ready")
        hook("UserPromptSubmit", prompt="PRIVATE PROMPT")
        self.assertEqual(self.state()["phase"], "running")
        hook("PermissionRequest", tool_name="Bash", tool_input={"command": "PRIVATE COMMAND"})
        self.assertTrue(self.state()["noticeID"])
        waiting = self.state()["noticeID"]
        hook("PostToolUse", tool_name="different_background_tool")
        self.assertEqual(self.state()["noticeID"], waiting)
        self.assertEqual(self.state()["phase"], "waiting")
        hook("PostToolUse", tool_name="Bash", tool_response="PRIVATE OUTPUT")
        self.assertEqual(self.state()["noticeID"], "")
        self.assertEqual(self.state()["phase"], "running")
        hook("PreToolUse", tool_name="request_user_input")
        self.assertEqual(self.state()["phase"], "waiting")
        hook("PostToolUse", tool_name="request_user_input")
        self.assertEqual(self.state()["noticeID"], "")
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "turn-id": "t1"})
        complete = self.state()["noticeID"]
        hook("PostToolUse", tool_name="Bash")
        self.assertEqual(self.state()["noticeID"], complete)
        self.assertEqual(self.state()["phase"], "complete")
        hook("UserPromptSubmit", turn="t2")
        self.assertEqual(self.state()["noticeID"], "")
        self.assertEqual(self.state()["phase"], "running")
        self.assertNotIn("PRIVATE", json.dumps(self.state()))

    def test_codex_old_callbacks_do_not_clear_or_recreate_current_attention(self):
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "UserPromptSubmit", "turn_id": "old"})
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "UserPromptSubmit", "turn_id": "new"})
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "turn-id": "old"})
        self.assertEqual(self.state()["noticeID"], "")
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "PermissionRequest", "turn_id": "new", "tool_name": "Bash"})
        notice = self.state()["noticeID"]
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "PostToolUse", "turn_id": "old", "tool_name": "Bash"})
        self.assertEqual(self.state()["noticeID"], notice)
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "UserPromptSubmit", "turn_id": "child", "agent_id": "subagent"})
        self.assertEqual(self.state()["noticeID"], notice)

    def test_auto_review_uses_matching_turn_context_and_never_marks_waiting(self):
        thread = str(uuid.uuid4())
        transcript = self.store / "rollout.jsonl"
        transcript.write_text("\n".join(map(json.dumps, [
            {"type": "session_meta", "payload": {"id": thread, "source": "cli"}},
            {"type": "turn_context", "payload": {"turn_id": "old", "approvals_reviewer": "user"}},
            {"type": "turn_context", "payload": {"turn_id": "new", "approvals_reviewer": "auto_review"}},
        ])) + "\n")
        payload = {"session_id": thread, "turn_id": "new", "transcript_path": str(transcript), "tool_name": "Bash"}
        bridge.on_codex_hook(self.store, self.session, {**payload, "hook_event_name": "UserPromptSubmit"})
        bridge.on_codex_hook(self.store, self.session, {**payload, "hook_event_name": "PermissionRequest", "permission_mode": "default"})
        self.assertEqual(self.state()["phase"], "running")
        self.assertEqual(self.state()["noticeID"], "")
        self.assertEqual(self.state()["approvalReviewer"], "auto_review")
        # Cached metadata remains useful after a long turn pushes context outside the bounded tail.
        transcript.unlink()
        self.assertEqual(bridge.codex_reviewer(self.store, self.session, payload), "auto_review")
        self.assertIsNone(bridge.codex_reviewer(self.store, self.session, {**payload, "turn_id": "other"}))

    def test_real_user_approval_and_question_remain_actionable(self):
        for tool in ["Bash", "functions.request_user_input", "functions.request_user_input_async"]:
            hook = "PermissionRequest" if tool == "Bash" else "PreToolUse"
            bridge.on_codex_hook(self.store, self.session, {"hook_event_name": hook, "tool_name": tool, "approvals_reviewer": "user"})
            self.assertEqual(self.state()["phase"], "waiting")
            self.assertEqual(self.state()["noticeReason"], "permission" if tool == "Bash" else "user_input")
            self.assertTrue(self.state()["noticeID"])

    def test_unknown_permission_route_is_explicit_and_not_claimed_to_be_user(self):
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "PermissionRequest", "permission_mode": "default", "tool_name": "Bash"})
        self.assertEqual(self.state()["noticeReason"], "permission")
        self.assertEqual(self.state()["approvalReviewer"], "")

    def test_old_unhooked_sessions_reject_guardian_and_unmapped_notify(self):
        home = self.store / "codex"
        folder = home / "sessions/2026/10/16"
        folder.mkdir(parents=True)
        parent, child, guardian = [str(uuid.uuid4()) for _ in range(3)]
        for thread, extra in [(parent, {"source": "cli"}), (child, {"source": {"subagent": {}}, "parent_thread_id": parent})]:
            (folder / f"rollout-{thread}.jsonl").write_text(json.dumps({"type": "session_meta", "payload": {"id": thread, **extra}}) + "\n")
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            before = self.state()
            for thread in [child, guardian]:
                bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "thread-id": thread, "turn-id": "background"})
                self.assertEqual(self.state(), before)
            bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "thread-id": parent, "turn-id": "main"})
            self.assertEqual(self.state()["phase"], "complete")
            self.assertEqual(self.state()["codexThreadID"], parent)
    def test_codex_interrupt_resolves_attention_and_session_end_preserves_completion(self):
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "PermissionRequest", "tool_name": "Bash"})
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "Interrupt"})
        self.assertEqual(self.state()["phase"], "idle")
        self.assertEqual(self.state()["noticeID"], "")
        bridge.on_notify(self.store, self.session, {"type": "agent-turn-complete", "turn-id": "final"})
        notice = self.state()["noticeID"]
        bridge.on_codex_hook(self.store, self.session, {"hook_event_name": "SessionEnd"})
        self.assertEqual(self.state()["noticeID"], notice)

    def test_codex_hooks_are_scoped_stable_and_preserve_trust_checks(self):
        first = bridge.launch_command("codex", "/fake/codex", self.store, self.session)
        second = bridge.launch_command("codex", "/fake/codex", self.store, str(uuid.uuid4()))
        config = next(arg for arg in first if arg.startswith("hooks="))
        self.assertIn(config, second)
        hooks = tomllib.loads(config)["hooks"]
        self.assertIn("UserPromptSubmit", hooks)
        self.assertIn("PermissionRequest", hooks)
        self.assertNotIn("Stop", hooks)  # Completion comes from notify, once the turn truly finishes.
        self.assertNotIn("--no-daemon", first)  # Dedicated app-server plus a real remote TUI.
        self.assertNotIn("--dangerously-bypass-hook-trust", first)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", first)
        for groups in hooks.values():
            command = groups[0]["hooks"][0]["command"]
            self.assertIn("codex-hook", command)
            self.assertNotIn(self.session, command)

    def test_codex_hook_entrypoint_is_silent_and_uses_session_environment(self):
        env = {**os.environ, "TERMINAL_PILOT_STORE": str(self.store), "TERMINAL_PILOT_SESSION": self.session}
        result = subprocess.run([sys.executable, str(SCRIPT), "codex-hook"],
                                input=json.dumps({"hook_event_name": "UserPromptSubmit", "turn_id": "t1", "prompt": "PRIVATE"}),
                                text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.state()["lifecycle"], "ready")
        self.assertEqual(self.state()["phase"], "running")
        self.assertNotIn("PRIVATE", json.dumps(self.state()))

    def test_subagent_and_unrelated_notification_do_not_complete_parent(self):
        for payload in [{"hook_event_name": "SubagentStop"},
                        {"hook_event_name": "Stop", "agent_id": "child"},
                        {"hook_event_name": "Notification", "notification_type": "auth_success"}]:
            bridge.on_hook(self.store, self.session, payload)
        self.assertEqual(self.state()["phase"], "connected")

    def test_two_same_project_sessions_are_isolated(self):
        other = str(uuid.uuid4())
        bridge.update(self.store, other, "running", tty="/dev/ttysOTHER", cwd=str(self.store))
        bridge.on_hook(self.store, self.session, {"hook_event_name": "Stop"})
        self.assertTrue(self.state()["noticeID"])
        self.assertEqual(json.loads(bridge.session_path(self.store, other).read_text())["phase"], "running")

    def test_concurrent_codex_retries_do_not_recreate_unread(self):
        payload = {"type": "agent-turn-complete", "turn-id": "same-turn"}
        bridge.on_notify(self.store, self.session, payload)
        notice = self.state()["noticeID"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: bridge.on_notify(self.store, self.session, payload), range(32)))
        self.assertEqual(self.state()["noticeID"], notice)

    def test_concurrent_writes_remain_valid_and_keep_metadata(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: bridge.update(self.store, self.session, "running", **{f"field{i}": i}), range(32)))
        state = self.state()
        self.assertEqual(len([k for k in state if k.startswith("field")]), 32)
        self.assertEqual(state["tty"], "/dev/ttysTEST")
        self.assertEqual(bridge.session_path(self.store, self.session).stat().st_mode & 0o777, 0o600)

    def test_invalid_session_cannot_escape_store(self):
        with self.assertRaises(ValueError):
            bridge.update(self.store, "../other", "complete")

    def test_real_hook_entrypoint(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "hook", "--store", str(self.store), "--session", self.session],
                                input=json.dumps({"hook_event_name": "Stop"}), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.state()["phase"], "complete")

    def test_launcher_preserves_extra_cli_arguments_and_paths(self):
        command = bridge.launch_command('codex', '/fake/codex', self.store, self.session, ['resume', "session ' $(literal)"])
        self.assertEqual(command[-2:], ['resume', "session ' $(literal)"])
        notify = json.loads(command[2].split('=', 1)[1])
        self.assertIn('notify', notify)
        self.assertIn(str(self.store), notify)

    def test_claude_settings_are_session_scoped(self):
        command = bridge.launch_command("claude", "/fake/claude", self.store, self.session, ["--resume", "x"])
        settings = json.loads(command[2])
        self.assertIn("Stop", settings["hooks"])
        self.assertIn("PermissionRequest", settings["hooks"])
        self.assertEqual(command[-2:], ["--resume", "x"])
        self.assertNotIn("permissions", settings)

    def test_resume_modes_preserve_arguments_cwd_and_notify(self):
        original = os.getcwd()
        try:
            for extra in [['resume'], ['resume', '--last'], ['resume', '--all'], ['resume', "session ' $(literal)"]]:
                captured = []
                def managed(binary, backend, args, env, register):
                    captured.append((binary, backend, args, env, os.getcwd()))
                    register('/tmp/tp-codex-test/server.sock')
                    return 0
                args = SimpleNamespace(store=str(self.store), session=self.session, cwd=str(self.store),
                                       tool='codex', label='Resume', extra=['--', *extra])
                with patch.object(bridge, 'run_managed', managed), patch.object(bridge.os, 'ttyname', return_value='/dev/ttys999'), \
                     patch.object(bridge.shutil, 'which', return_value=sys.executable):
                    with self.assertRaises(SystemExit) as result: bridge.launch(args)
                self.assertEqual(result.exception.code, 0)
                binary, backend, forwarded, env, cwd = captured[0]
                self.assertEqual(forwarded, extra)
                self.assertEqual(Path(cwd).resolve(), self.store.resolve())
                self.assertEqual(env['TERMINAL_PILOT_SESSION'], self.session)
                self.assertEqual(env['TERMINAL_PILOT_STORE'], str(self.store.resolve()))
                self.assertIn('notify', json.loads(backend[1].split('=', 1)[1]))
                self.assertNotIn('--no-daemon', backend)
        finally: os.chdir(original)

    def test_existing_codex_notify_is_read_without_copying_config(self):
        config_home = self.store / "codex-home"
        config_home.mkdir()
        (config_home / "config.toml").write_text('notify = ["/example/existing-notify"]\n')
        with patch.dict(os.environ, {"CODEX_HOME": str(config_home)}):
            self.assertEqual(bridge.upstream_notify(self.store), ["/example/existing-notify"])

    def test_claude_resume_modes_preserve_cwd_and_hooks(self):
        fake = self.store / "claude"
        output = self.store / "claude-resume.json"
        # A Unix shebang cannot quote an interpreter path containing spaces.
        (self.store / "pilot-test-python").symlink_to(sys.executable)
        fake.write_text(f"#!/usr/bin/env pilot-test-python\nimport json,sys,os\nopen({str(output)!r}, 'w').write(json.dumps([sys.argv[1:],os.getcwd(),os.environ['TERMINAL_PILOT_SESSION']]))\n")
        fake.chmod(0o700)
        for extra in [[], ["--resume"], ["--continue"], ["--resume", "session ' $(literal)"]]:
            with self.subTest(extra=extra):
                master, slave = os.openpty()
                try:
                    command = [sys.executable, str(SCRIPT), "launch", "claude", "--store", str(self.store),
                               "--cwd", str(self.store), "--label", "Resume", "--", *extra]
                    result = subprocess.run(command, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            env={**os.environ, "PATH": str(self.store) + os.pathsep + os.environ["PATH"]})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    args, cwd, session = json.loads(output.read_text())
                    self.assertEqual(args[0], "--settings")
                    self.assertEqual(args[2:], extra)
                    self.assertEqual(Path(cwd).resolve(), self.store.resolve())
                    self.assertTrue(bridge.session_path(self.store, session).exists())
                    hooks = json.loads(args[1])["hooks"]
                    for name in ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "PermissionRequest"]:
                        self.assertIn(name, hooks)
                        self.assertIn(session, hooks[name][0]["hooks"][0]["command"])
                finally:
                    os.close(master)
                    os.close(slave)


if __name__ == "__main__":
    unittest.main()
