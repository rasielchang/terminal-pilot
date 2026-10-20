#!/usr/bin/env python3
"""Local event bridge. Never stores prompts, responses, transcripts or credentials."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

# Hooks can run independently of the app; keep its signed resources immutable.
sys.dont_write_bytecode = True
from codex_managed import run_managed


def default_store():
    return Path.home() / "Library/Application Support/Terminal Pilot"


def session_path(store, session):
    # Session IDs are ours, not model-provided paths.
    return Path(store) / "sessions" / (str(uuid.UUID(session)) + ".json")


def update(store, session, phase, *, clear_notice=False, resolve_waiting=False,
           begin_turn=None, event_turn=None, progress_tool=None, tool_finished=False,
           notification_thread=None, preserve_waiting_tool=False, runtime_observation=None,
           runtime_binding=None, runtime_event=None, **fields):
    target = session_path(store, session)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = target.with_suffix(".lock")
    with open(lock, "a") as stream:
        os.chmod(lock, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            state = json.loads(target.read_text()) if target.exists() else {}
            if runtime_binding is not None:
                expected, thread = runtime_binding
                if (state.get('pid') != expected.get('agentPID') or state.get('codexSocket') != expected.get('codexSocket')
                        or state.get('phase') == 'ended' or state.get('codexThreadID') not in {None, '', thread}):
                    return state
                phase = state.get('phase', 'connected')
                fields = {'codexThreadID': thread, 'codexThreadSource': 'runtime', 'lifecycle': 'runtime'}
            if runtime_event is not None:
                expected, method, turn, status = runtime_event
                if (state.get('pid') != expected.get('agentPID') or state.get('codexSocket') != expected.get('codexSocket')
                        or state.get('codexThreadID') != expected.get('codexThreadID') or state.get('phase') == 'ended'):
                    return state
                if method == 'turn/started':
                    begin_turn, phase, clear_notice = turn, 'running', True
                elif method == 'turn/completed':
                    event_turn = turn
                    phase = {'completed': 'complete', 'failed': 'error', 'interrupted': 'idle'}.get(status, 'idle')
                    fields = {'lastTurn': turn}
                    resolve_waiting = True
            if runtime_observation is not None:
                expected, runtime = runtime_observation
                if (state.get("tool") != "codex" or state.get("phase") == "ended"
                        or any(state.get(field) != expected.get(field) for field in
                               ("codexThreadID", "pid", "updatedAt"))):
                    return state  # Late reads cannot overwrite a newer hook or another process.
                if runtime in {"approval", "question"}:
                    phase = "waiting"
                    waiting_keys = expected.get('pendingApprovalKeys') or []
                    known_keys = state.get('seenApprovalKeys', [])
                    new_requests = set(waiting_keys) - set(known_keys)
                    fields = {"noticeReason": "permission" if runtime == "approval" else "user_input",
                              "approvalReviewer": "user", "waitingTool": ""}
                    if new_requests:
                        fields['seenApprovalKeys'] = (known_keys + sorted(new_requests))[-128:]
                    if (state.get("phase") == phase and state.get("approvalReviewer") == "user"
                            and state.get("noticeReason") == fields["noticeReason"] and not new_requests):
                        return state  # Polling never creates a second notice for the same wait.
                elif runtime == "running":
                    phase, clear_notice = "running", True
                    if state.get("phase") == phase and not state.get("noticeID"):
                        return state
                elif runtime == "idle" and state.get("phase") in {"waiting", "running"}:
                    phase, resolve_waiting = "idle", True
                else:
                    return state  # Completion/error notices remain owned by agent lifecycle events.
            # Some CLI builds also invoke notify for background/guardian threads.
            # Lifecycle hooks identify the actual TUI conversation authoritatively.
            if notification_thread and state.get("codexThreadSource") in {"hook", "runtime"} and state.get("codexThreadID") != notification_thread:
                return state
            if begin_turn:
                previous = state.get("activeTurn")
                if previous and previous != begin_turn:
                    state["supersededTurns"] = (state.get("supersededTurns", []) + [previous])[-32:]
                if previous != begin_turn or not state.get('activeTurnStartedAt'):
                    state['activeTurnStartedAt'] = time.time()
                state["activeTurn"] = begin_turn
            elif event_turn and state.get("activeTurn") and state["activeTurn"] != event_turn:
                return state  # A delayed tool callback must not change a newer turn.
            if progress_tool is not None and state.get("phase") == "waiting" and state.get("waitingTool") not in {None, progress_tool}:
                return state  # Other background work cannot resolve this question/approval.
            if tool_finished and state.get("phase") in {"complete", "error", "ended", "idle"}:
                return state
            if fields.get("lastTurn") in state.get("supersededTurns", []):
                return state
            if phase == "complete" and fields.get("lastTurn") and state.get("lastTurn") == fields["lastTurn"]:
                return state
            if (preserve_waiting_tool and not fields.get("waitingTool")
                    and state.get("phase") == "waiting" and state.get("noticeReason") == "permission"):
                fields.pop("waitingTool", None)
            now = time.time()
            state.update(fields)
            state.update(id=session, phase=phase, updatedAt=now)
            state.setdefault("createdAt", now)
            state.setdefault("noticeID", "")
            state.setdefault("noticeAt", 0)
            if phase in {"complete", "waiting", "error", "attention"}:
                state["noticeID"] = str(uuid.uuid4())
                state["noticeAt"] = now
                state["noticePhase"] = phase
            elif phase in {"running", "idle"} and (clear_notice or (resolve_waiting and state.get("noticePhase") == "waiting")):
                state["noticeID"] = ""
            fd, name = tempfile.mkstemp(dir=target.parent, prefix=".event-")
            try:
                with os.fdopen(fd, "w") as out:
                    json.dump(state, out, ensure_ascii=False)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(name, target)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
    return state


def observe_codex_runtime(store, session, runtime):
    if runtime not in {"approval", "question", "running", "idle"}:
        return
    expected = {"codexThreadID": session.get("codexThreadID"), "pid": session.get("agentPID"),
                "updatedAt": session.get("updatedAt"), 'pendingApprovalKeys':session.get('pendingApprovalKeys', [])}
    update(store, session["id"], "idle", runtime_observation=(expected, runtime))


def bind_codex_runtime(store, session, thread):
    update(store, session['id'], 'connected', runtime_binding=(session, thread))


def codex_runtime_event(store, session, event):
    _, method, turn, status = event
    update(store, session['id'], 'idle', runtime_event=(session, method, turn, status))


def claude_phase(payload):
    event = payload.get("hook_event_name")
    if payload.get("agent_id") or payload.get("agent_type"):
        return None  # A child agent finishing is not the parent finishing.
    if event == "PreToolUse" and payload.get("tool_name") == "AskUserQuestion":
        return "waiting"
    if event == "Notification":
        return "waiting" if payload.get("notification_type") in {
            "permission_prompt", "elicitation_dialog", "elicitation_url_dialog"
        } else None
    return {
        "SessionStart": "connected", "UserPromptSubmit": "running",
        "PreToolUse": "running",
        "PostToolUse": "running", "PermissionRequest": "waiting",
        "Stop": "complete", "StopFailure": "error", "SessionEnd": "ended",
    }.get(event)


def on_hook(store, session, payload):
    phase = claude_phase(payload)
    if phase:
        # User input supersedes the previous notice. Tool progress only resolves
        # a waiting reminder; it is not evidence that a completion/error was read.
        hook = payload.get("hook_event_name")
        fields = {}
        try:
            fields["claudeSessionID"] = str(uuid.UUID(payload.get("session_id")))
            fields["claudeHome"] = str(Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser().resolve())
            transcript = payload.get("transcript_path")
            fields["claudeTranscript"] = str(Path(transcript).expanduser().resolve()) if isinstance(transcript, str) and transcript else ""
        except (ValueError, TypeError, AttributeError):
            pass
        notification = payload.get("notification_type")
        if phase == "waiting":
            fields["waitingTool"] = payload.get("tool_name")
            fields["noticeReason"] = "permission" if (hook == "PermissionRequest" or notification == "permission_prompt") and payload.get("tool_name") != "AskUserQuestion" else "user_input"
            # A permission hook may still be resolved automatically. The actual
            # permission notification confirms a human-facing prompt.
            fields["approvalReviewer"] = "user" if notification == "permission_prompt" else ""
        elif phase == "complete":
            fields["noticeReason"] = "completion"
        update(store, session, phase, clear_notice=hook in {"UserPromptSubmit", "PreToolUse"},
               preserve_waiting_tool=notification == "permission_prompt",
               progress_tool=payload.get("tool_name") if hook in {"PreToolUse", "PostToolUse"} else None,
               resolve_waiting=hook == "PostToolUse", **fields)


def thread_metadata(value):
    try:
        return {"codexThreadID": str(uuid.UUID(value))}
    except (ValueError, TypeError, AttributeError):
        return {}


def codex_metadata(payload, thread_key):
    fields = thread_metadata(payload.get(thread_key))
    fields["codexHome"] = str(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve())
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        fields["codexTranscript"] = str(Path(transcript).expanduser().resolve())
    return fields


USER_INPUT_TOOLS = {"request_user_input", "functions.request_user_input",
                    "request_user_input_async", "functions.request_user_input_async"}


def codex_reviewer(store, session, payload):
    """Use the exact turn's effective reviewer, never the global default or permission_mode.

    PermissionRequest is emitted BEFORE Codex auto-review and is not proof of a
    human approval prompt. Current hook payloads omit the reviewer, but the
    matching turn_context in the CLI's own rollout records it.
    """
    known = {"user", "auto_review", "guardian_subagent"}
    if payload.get("approvals_reviewer") in known:
        return payload["approvals_reviewer"]
    turn = payload.get("turn_id")
    if not turn:
        return None
    try:
        previous = json.loads(session_path(store, session).read_text())
        if (previous.get("reviewerTurn") == turn and previous.get("codexThreadID") == payload.get("session_id")
                and previous.get("approvalReviewer") in known):
            return previous["approvalReviewer"]
        path = payload.get("transcript_path")
        if not isinstance(path, str) or not path:
            return None
        with Path(path).open("rb") as stream:
            meta = json.loads(stream.readline(256_000)).get("payload", {})
            if meta.get("id", meta.get("session_id")) != payload.get("session_id"):
                return None
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 8 * 1024 * 1024))
            if stream.tell():
                stream.readline()
            for line in reversed(stream.read().splitlines()):
                if b'"turn_context"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                context = record.get("payload", {})
                if record.get("type") == "turn_context" and context.get("turn_id") == turn:
                    reviewer = context.get("approvals_reviewer")
                    return reviewer if reviewer in known else None
    except (OSError, ValueError, TypeError):
        pass
    return None


def primary_codex_notify(store, session, payload):
    """Old sessions without trusted hooks must also reject guardian/child notify."""
    thread = thread_metadata(payload.get("thread-id")).get("codexThreadID")
    if not thread:
        return True  # Compatibility with old notify payloads without thread IDs.
    try:
        old = json.loads(session_path(store, session).read_text())
        if old.get("codexThreadSource") == "hook":
            return old.get("codexThreadID") == thread
    except (OSError, ValueError):
        pass
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    for path in home.glob(f"sessions/*/*/*/*{thread}.jsonl"):
        try:
            if not path.resolve().is_relative_to(home / "sessions"):
                continue
            with path.open("rb") as stream:
                meta = json.loads(stream.readline(256_000)).get("payload", {})
            if meta.get("id", meta.get("session_id")) == thread:
                return meta.get("source") == "cli" and not meta.get("parent_thread_id")
        except (OSError, ValueError, TypeError):
            continue
    # Internal non-persisted guardian threads must not overwrite an unhooked main session.
    return False


def on_notify(store, session, payload):
    if payload.get("agent_id") or payload.get("agent_type"):
        return
    if payload.get("type") == "agent-turn-complete":
        if not primary_codex_notify(store, session, payload):
            return
        # Stable event identity makes retries idempotent.
        event_key = payload.get("turn-id")
        target = session_path(store, session)
        if event_key and target.exists():
            old = json.loads(target.read_text())
            if old.get("lastTurn") == event_key:
                return
        update(store, session, "complete", lastTurn=event_key, notification_thread=payload.get("thread-id"),
               noticeReason="completion",
               **codex_metadata(payload, "thread-id"))


def on_codex_hook(store, session, payload):
    """Observe lifecycle only; never return decisions, prompts or tool output."""
    if payload.get("agent_id") or payload.get("agent_type"):
        return
    hook = payload.get("hook_event_name")
    tool = payload.get("tool_name", "")
    waiting_input = hook == "PreToolUse" and tool in USER_INPUT_TOOLS
    phase = "waiting" if waiting_input else {
        "SessionStart": "connected", "UserPromptSubmit": "running",
        "PreToolUse": "running", "PermissionRequest": "waiting", "PostToolUse": "running",
        "Interrupt": "idle", "SessionEnd": "ended",
    }.get(hook)
    if phase is None:
        return
    reviewer = codex_reviewer(store, session, payload)
    if hook == "PermissionRequest" and reviewer in {"auto_review", "guardian_subagent"}:
        phase = "running"
    # SessionStart also fires after compaction, while the same turn is working.
    if hook == "SessionStart" and payload.get("source") == "compact":
        phase = "running"
    update(store, session, phase, clear_notice=phase in {"running", "idle"},
           begin_turn=payload.get("turn_id") if hook == "UserPromptSubmit" else None,
           event_turn=payload.get("turn_id") if hook != "UserPromptSubmit" else None,
           progress_tool=tool if hook in {"PreToolUse", "PostToolUse", "PermissionRequest"} and phase == "running" else None,
           tool_finished=hook == "PostToolUse",
           **({"waitingTool": tool, "noticeReason": "user_input" if waiting_input else "permission"} if phase == "waiting" else {}),
           approvalReviewer=reviewer or "", reviewerTurn=payload.get("turn_id") or "", lifecycle="ready",
           **codex_metadata(payload, "session_id"),
           **({"codexThreadSource": "hook"} if thread_metadata(payload.get("session_id")) else {}))


def upstream_notify(cwd):
    """Read only known config paths. Do not copy config or secrets into app data."""
    import tomllib
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    paths = [codex_home / "config.toml"]
    # Include project layers from root to cwd, matching config precedence.
    folder = Path(cwd).resolve()
    paths += [p / ".codex/config.toml" for p in [*reversed(folder.parents), folder]]
    command = None
    for path in dict.fromkeys(paths):
        if path.is_file():
            config = tomllib.loads(path.read_text())
            if "notify" in config:
                command = config["notify"]
    if not isinstance(command, list) or not all(isinstance(v, str) for v in command):
        return None
    # Prevent accidentally forwarding to ourselves.
    if any(Path(v).name == Path(__file__).name for v in command):
        return None
    return command


def claude_settings(store, session):
    command = shlex.join([sys.executable, "-B", str(Path(__file__).resolve()), "hook",
                          "--store", str(store), "--session", session])
    events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
              "Notification", "Stop", "StopFailure", "SessionEnd"]
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": command,
                                        "timeout": 3}]}] for event in events}}


def codex_hooks():
    # Keep definitions stable across launches so Codex can remember the user's
    # trust choice. Each process receives its own destination in the environment.
    command = shlex.join([sys.executable, "-B", str(Path(__file__).resolve()), "codex-hook"])
    events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
              "PostToolUse", "Interrupt", "SessionEnd"]
    return {event: [{"hooks": [{"type": "command", "command": command, "timeout": 3}]}]
            for event in events}


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(map(toml_value, value)) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(toml_value(k) + " = " + toml_value(v) for k, v in value.items()) + "}"
    raise TypeError("Unsupported TOML value")


def launch_command(tool, binary, store, session, extra=()):
    if tool == "claude":
        return [binary, "--settings", json.dumps(claude_settings(store, session)), *extra]
    notify = [sys.executable, "-B", str(Path(__file__).resolve()), "notify",
              "--store", str(store), "--session", session]
    if os.environ.get("TERMINAL_PILOT_TEST_NO_FORWARD") == "1":
        notify += ["--no-forward"]
    # A dedicated app-server keeps hook environment/session routing isolated.
    # Normal Codex hook trust checks remain enabled; no user config is overwritten.
    return [binary, "-c", "notify=" + json.dumps(notify),
            "-c", "hooks=" + toml_value(codex_hooks()), *extra]


def launch(args):
    store = Path(args.store).expanduser().resolve()
    os.chdir(Path(args.cwd).expanduser())
    session = args.session or str(uuid.uuid4())
    tty = os.ttyname(sys.stdin.fileno())
    from runtime_paths import find_tool
    binary = find_tool(args.tool)
    if not binary or not os.access(binary, os.X_OK):
        raise RuntimeError(f"找不到 {args.tool}，請先安裝 CLI。")
    extra = args.extra[1:] if args.extra[:1] == ["--"] else args.extra
    update(store, session, "connected", tty=tty, tool=args.tool,
           cwd=os.getcwd(), label=args.label or Path.cwd().name, pid=os.getpid(),
           lifecycle="pending" if args.tool == "codex" else "ready")
    command = launch_command(args.tool, binary, store, session, extra)
    env = os.environ.copy()
    env["TERMINAL_PILOT_STORE"] = str(store)
    env["TERMINAL_PILOT_SESSION"] = session
    if args.tool == 'codex' and getattr(args, 'codex_home', None):
        env['CODEX_HOME'] = str(Path(args.codex_home).expanduser().resolve())
    # A bundled absolute Codex launcher still needs its Node directory on PATH.
    env["PATH"] = str(Path(binary).parent) + os.pathsep + env.get("PATH", "")
    print("Terminal Pilot 已接上完成通知。這仍是 macOS Terminal 的獨立視窗。", flush=True)
    if args.tool == "codex":
        try:
            result = run_managed(binary, launch_command('codex', binary, store, session)[1:], extra, env,
                lambda socket: update(store, session, 'connected', codexSocket=socket,
                                      codexHome=env.get('CODEX_HOME', str(Path.home() / '.codex'))))
        finally:
            update(store, session, 'ended', clear_notice=True, codexSocket='')
        raise SystemExit(result)
    os.execvpe(binary, command, env)


def simulate(args):
    session = args.session or str(uuid.uuid4())
    tty = os.ttyname(sys.stdin.fileno())
    update(args.store, session, "running", tty=tty, tool="demo", cwd=os.getcwd(),
           label="通知測試（非 AI）", pid=os.getpid())
    print("這是通知管線測試，不會呼叫 AI。可先從管理器收起此視窗。", flush=True)
    for seconds in range(args.seconds, 0, -1):
        print(f"{seconds} 秒後回報完成…", flush=True)
        time.sleep(1)
    on_notify(args.store, session, {"type": "agent-turn-complete", "turn-id": "demo"})
    print("已回報完成。管理器應出現紅點；雙擊、點「叫回」或「已處理」可清除提醒。", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ["launch", "hook", "codex-hook", "notify", "simulate"]:
        command = commands.add_parser(name)
        command.add_argument("--store", default=str(default_store()))
        command.add_argument("--session", required=name in {"hook", "notify"})
        if name == "launch":
            command.add_argument("tool", choices=["claude", "codex"])
            command.add_argument("--cwd", default=os.getcwd())
            command.add_argument("--label", default="")
            command.add_argument("--codex-home", default=None)
            command.add_argument("extra", nargs="*")
        if name == "simulate":
            command.add_argument("--seconds", type=int, default=10)
        if name == "notify":
            command.add_argument("--no-forward", action="store_true", help=argparse.SUPPRESS)
            command.add_argument("payload")
    args, unknown = parser.parse_known_args()
    if args.action == "launch":
        args.extra += unknown
        launch(args)
    elif args.action == "simulate":
        simulate(args)
    else:
        try:
            if args.action == "codex-hook":
                store = os.environ.get("TERMINAL_PILOT_STORE")
                session = os.environ.get("TERMINAL_PILOT_SESSION")
                if store and session:
                    on_codex_hook(store, session, json.load(sys.stdin))
            elif args.action == "hook":
                on_hook(args.store, args.session, json.load(sys.stdin))
            else:
                payload = json.loads(args.payload)
                on_notify(args.store, args.session, payload)
                previous = None if args.no_forward else upstream_notify(payload.get("cwd", os.getcwd()))
                if previous:
                    subprocess.Popen([*previous, args.payload], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        except Exception:
            # Notification integration must not block or alter the AI's turn.
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
