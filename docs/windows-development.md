# Windows development and Git synchronization

This branch integrates the existing Windows background Brain host. Use native
PowerShell and the existing Codex CLI. WSL and tmux are not dependencies. It does
not implement the planned Windows Terminal / ConPTY desktop manager or provide a
Windows installer. The Mac app, Swift sources, packaging, Keychain integration,
licenses and original Mac CI checks remain in place.

## Existing Windows components

- `scripts/windows_brain.py`: native start/status/stop launcher.
- `scripts/brain_server.py`: single-user loopback HTTP service, persistent pairing,
  exact conversation resume, approval cards and optional private Tailscale HTTPS.
- `scripts/brain_stdio.py`: owned Codex app-server subprocess over stdio.
- `scripts/pilot_http.py`: HTTP boundary shared with the Mac host. Authentication,
  origin checks, request limits and security headers are retained.
- `scripts/brain_permissions.py`: optional local development write scope. Default
  sessions remain read-only. Explicit roots retain the sandbox, restricted network
  and on-request approvals; do not distribute your development profile.

The browser retains its assistant/manager modes, exact raw conversation reader,
notification settings and elapsed request display. Elapsed time includes queueing
and approval waits; it is not CPU time or a completion estimate.

### Shared HTTP read receipts and unsupported operations

Both hosts require the same Bearer authentication and Host/Origin checks.
`POST /api/ack` accepts only `session` and `noticeID` on the background host.
It returns `200 {"acknowledged":true}` only for that exact current notice,
including an idempotent retry. A newer or resolved notice returns
`200 {"acknowledged":false}`; malformed input or an unknown session returns 409.
The read receipt survives service restart. It does not answer an approval, remove
its card, start a turn, or acknowledge a separate Brain matter result.
Decisions still require the existing exact binding and approval handle at
`POST /api/approval`; Brain matter reads use `POST /api/brain/ack`.

The background host has no native Terminal windows or directory picker.
Directory listing/creation, Terminal read/input/close, launch, direct send and
approval upgrade return HTTP 501 with
`{"code":"unsupported_operation","error":"..."}`. Clients must show the error,
not treat it as success or retry it as an uncertain side effect. The Mac host
continues to delegate these operations to its existing native bridge.

## Native PowerShell setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 scripts\windows_brain.py start
.\.venv\Scripts\python.exe -X utf8 scripts\windows_brain.py status
```

Install and authenticate the Codex CLI using its supported login flow first. The
launcher discovers the native npm-installed executable; `--codex-binary` supports
another installation. `--codex-home`, `--brain-root`, `--store` and `--port` allow
explicit per-user configuration. Never copy a developer's credentials or Codex
home to another employee. The default store is `.runtime/windows-brain`, excluded
from Git and public export. Keep pairing codes out of commits and screenshots.

This service is single-user, not employee SSO or tenant isolation. Keep it on
localhost or a trusted private network. Voice still depends on the Mac Keychain
integration and is not a validated Windows feature.

`start --restart` retains the existing store and conversation; use it only after
current work has finished. `--tailscale` configures the dedicated private HTTPS
listener when Tailscale is installed and connected. Do not use public Funnel or
port forwarding as a shortcut.

## Verification

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\test-all.py
.\.venv\Scripts\python.exe -X utf8 -m compileall -q scripts
git diff --check
```

On Windows the runner explicitly reports Swift model checks as NOT RUN. Tests for
the Mac bridge, POSIX sockets/signals, Finder and unavailable symlink privileges
are skipped, not counted as passing. The Mac CI job still runs its original Swift
and packaging checks. Windows CI runs Python/JavaScript regressions and compilation;
it does not build an installer. Real Windows Terminal interaction, Mac Automation,
Keychain, phone microphone/push and installers require their respective devices.

## Migration and future work

The unmodified source-transfer ZIP is the common ancestor. Only identified Windows
changes are merged onto remote main; no source deletions were needed. The Tailscale
conflict preserves main's configured-tool priority and Mac discovery and adds the
existing Windows Program Files fallback. Public MIT licensing, third-party notices,
portable runtime, newer documentation and Mac packaging are retained.

The original Windows folder is preserved and fully backed up outside this Git
checkout. Backups, hash manifests, personal settings, workspaces, histories,
databases, authentication, logs, build output and `.venv` must stay out of Git.

Continue development in the new Git clone, not the preserved ZIP-derived folder.
The running service may still use the original folder; committing code does not
deploy it. Moving a runtime is a separate, idle-time operation that preserves
private state outside tracked sources.

```powershell
git fetch origin
git status --short
git diff --check
git diff --cached --stat
git diff --cached
.\.venv\Scripts\python.exe -X utf8 scripts\check-public.py --staged
git push -u origin feature/windows-port
```

Stage explicit source paths and review every deletion and staged change. The staged
scan reads Git index blobs, including new directories; the normal scan also covers
source paths beyond the export allowlist. Unlisted source directories cause a
finding rather than silently disappearing from the audit. The source exporter
includes audited Windows files and uses portable `/` paths in its manifest. Do not
force push, push directly to main, merge to main or publish an installer as part of
this migration.
