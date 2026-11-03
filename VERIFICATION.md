# Verification scope

This file contains only public, synthetic-test information. Local acceptance
notes and private project details are excluded from the source distribution.

## Task isolation and partial window health — build 54

- New-task actions cannot resume or dispatch into an unrelated existing thread. Fixtures cover Codex/Claude new launches, empty-thread conflicts, exact continuation ownership, old-thread launch rejection, lost launch responses across restart and idempotent receipts. Existing continuation fixtures now declare their original task binding explicitly.
- Partial-window fixtures keep confirmed work usable, block retained unverified rows, replace a verified moved TTY's old location, retain unknown closure status and avoid rewriting identical diagnostics on every poll. The diagnostic contains counts, IDs and error kinds, not Terminal contents.
- Local offline run: 418 Python tests (416 passed, two opt-in suites skipped), 305 Swift checks and all 25 JavaScript suites passed.
- The optional real-Codex/local-Postman run passed its protocol assertions twice, but exited with a temporary plugin-clone directory cleanup race. This is recorded as an incomplete optional test run, not a clean pass. No real model or live engineering session was used.
- Real-model intent classification, true iPhone use and Windows host deployment remain unverified. The API checks exact task ownership; it cannot determine from prose alone whether two goals are semantically the same task. The existing Windows planner does not gain the Mac unified-action route guard in this change.

## Terminal window discovery — regression scope, 2026-11-03

- Missing-ID fixtures cover partial cold-start lists on Mac/PWA, retained previous rows, normal retries, reliable recovery and closure remaining unknown for an incomplete inventory. `missing value` is never coerced into a window ID.

- Isolated Swift fixtures exercise one bounded retry for a missing window, propagation after repeated failure, and no retry for an Automation denial. A malformed row fails the whole snapshot instead of silently becoming evidence that a work was closed.
- Model tests keep existing work rows and `snapshotReliable=false` during a failed read, recover on the next normal poll, remove the stale error, and preserve explicit reconnection after denied permission.
- The exact AppleScript snapshot source is compiled in tests without execution. Complete descriptors preserve window/TTY identity. These tests do not operate real Terminal windows; live list recovery is a separate installation check.

## Attention contract — isolated verification, 2026-11-03

- The acceptance suite calls the production scheduler and notification selector with isolated stores and synthetic model/push IO: ten direct exchanges produce zero Brain model calls/pushes; one delegated outcome produces one integration request and one push identity in either source/job arrival order and across restart.
- Repeated scans, transient gaps/recovery and summary projections produce zero model calls/pushes. Distinct questions, actual failures and formal approval cards remain visible. Read, answered and resolved states are tested separately, including desktop/PWA and stale-event races.
- Actual local Codex app-server with the existing Postman synthetic provider verifies exact queued-message retirement without deleting a separate human entry, one formal delegated result returning to the same Brain, no observation inference, failure receipts, and context/explicit-skill behavior. No real model or live engineering session is used.
- Synthetic context probe: 600 progress events plus ten unowned results invoke zero models. A useful later query carries a 9,055-byte checkpoint; unchanged delta is 494 bytes. Bytes are not billed token measurements.
- Final local offline run: 408 Python tests (406 passed, two opt-in protocol suites skipped), 274 Swift model checks, and all 25 JavaScript suites passed. The separate real-Codex/local-mock integration run passed.
- Real iPhone notifications/voice/background behavior, real-model judgment, Windows Terminal deployment and live multi-session behavior are not established by these fixtures. The local candidate is an ad-hoc portable build, not a new public release or notarized distribution.

## Assistant orchestration — isolated verification, 2026-10-30

- Durable closed-window resume, same-project distinct conversations, new-project work labels, launch/send deduplication, restart/lost replies, manual takeover, busy/approval waits, result consolidation and direct mobile navigation have synthetic regression coverage.
- Actual local Codex app-server was tested with isolated HOME/CODEX_HOME, synthetic history and a local Postman Responses fixture. Source-data skill mentions caused no skill expansion; an explicit user skill request loaded only that skill. No real model, user credentials or live engineering session was used.
- Checkpoint/delta context comparison uses the old and new `_context` methods with the same synthetic fixture (12 sessions, 16 history messages, eight unrelated project memories). Old prompt: 143,125 UTF-8 bytes; new checkpoint: 20,486 bytes (85.7% less); unchanged-state follow-up: 639 bytes (99.6% less). These are transport text bytes, **not** measured billed token counts; actual savings depend on changing state and model history.
- Compaction and reconnect recovery use formal event/revision fixtures. Live automatic model compaction is not claimed as tested.
- Final local offline checks: 243 Python tests (241 passed, two opt-in tests skipped), 242 Swift model checks, and all 22 JavaScript suites passed. Public-source scan found no issues.
- Native launch tests use temporary stores and a fake Terminal bridge. No installation, Pilot restart, real iPhone acceptance or Windows Server deployment was performed for this change. CI results are recorded on the PR for the exact head.

## 0.13.0 Preview 1 — local verification, 2026-10-20

The existing suites cover native window targeting, unread races, saved projects,
mobile navigation, input/approval guards, push, voice, Brain and socket transport.
Packaging checks add clean-user runtime discovery, explicit missing-tool errors,
Keychain worker boundaries, hash verification and relocation of the complete App.

Environment: Apple Silicon, macOS 14.5; portable Python 3.13.16 and Node 22.23.2.

- Python: 190 tests discovered, 189 passed, 1 opt-in live Codex protocol test skipped.
- Swift: 205 model checks passed using temporary stores and a fake Terminal bridge.
- JavaScript: all 17 offline suites passed.
- Portable App: imports, TLS roots, native-library dependency scan and actual bundled
  Node/ws handshake passed after relocation to a path with spaces, a fresh HOME
  and minimal PATH. Transport uses an isolated synthetic socket pair, not a live agent.
- Strict/deep local code-signature verification passed before and after runtime tests.
  Test subprocesses and production Python entry points suppress bytecode writes to
  preserve sealed App resources.
- Native UI: orderly quit, confirmed process exit, replacement and relaunch succeeded.
  Existing work list remained present; setup displayed installed tools and existing
  Keychain status; native web controls reported the service ready. No agent was sent
  a test prompt, and no new terminal was created.
- Public-source allowlist and credential/path/document-link scan passed. Export
  includes a per-file SHA-256 manifest; local state and private notes are excluded.
- Release packaging completed in the maintainer's local Terminal: Developer ID
  signing with hardened runtime, strict/deep signature and portable-runtime checks,
  Apple notarization, stapling and ticket validation, and Gatekeeper assessment.
- The final release ZIP's SHA-256 matches its checksum file. Extracted App files
  match the signed input byte for byte, with the stapled notarization ticket added.

Limits: this run did not exercise a live model, a new key's API validation/save,
first-time Automation consent, iPhone devices, or a second physical Mac. Browser
acceptance was unavailable in this environment; sandbox policy also blocked a live
loopback HTTP probe. HTTP contract/static-asset tests use the isolated test harness.
The opt-in Codex protocol test and GitHub CI have not run for this preview.

The public release ZIP is **Developer ID signed and Apple notarized**. Gatekeeper
acceptance above is the local packaging check; installation after downloading from
GitHub on a clean account or another Mac has not yet been tested. A repeat of the
system signing checks in restricted automation encountered system-service errors
and is not counted as an additional verification pass. Signing and notarization do
not establish feature stability or broad device compatibility.
