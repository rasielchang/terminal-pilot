# Work observation and report-only delivery

This depends on the [observation core](work-observation.md). The observer keeps
watching Pilot-registered, unfinished exact conversations even without an active
dispatch job and after a job result or window close. It never scans arbitrary
agent homes or treats a worker's instructions as new human authority.

Important source events are journaled and batched into the **existing Brain
thread**, with a durable event ledger and stable queue client ID. The ingestion cursor is
not a model-completion receipt. Each
batch holds at most eight important events / approximately 16 KB of event data.
Ordinary progress and short receipt acknowledgments are journaled without model
turns. Claude tool-use commentary retains its native phase. Related substantive
updates settle for 15 seconds (maximum 30 seconds from the first pending event);
new human waits, failures and source gaps bypass that interval. Pending delivery
applies backpressure; new events still enter the durable pending ledger. Exact
source IDs survive restart/cursor replay; equal text with different IDs is not
deduplicated. Summary/projection events never enter a report batch. Context omits
old results/summaries unrelated to the fresh batch. The original
Brain's natural reply is the only chat response, shared by the desktop TUI and
web/PWA. There is no second transcript or briefing scheduler.

Report-only contexts have no dispatch, launch, release, task-mutation or close
capabilities. Both the action schema and server enforce this. Explicit human
turns retain the existing tools and approval policy. Existing scheduler result
turns are also report-only when observation is enabled. Source results/waits
supersede duplicate job-level reports when the exact source is current.

Disconnected or ambiguous Brain bindings retain pending delivery without opening
a window. A changed Brain thread cannot inherit that delivery. Transient
transport errors reconcile the same request with bounded backoff (up to five
minutes), never a second worker command. A known failed/cancelled model turn is
retained as a failed delivery plus visible error; it is not resurrected. Queries
and the next briefing can recover its source events. This is not a promise of
exactly-once provider inference under every upstream failure.

Semantic summaries have explicit original event references and a current work
revision. Late or cross-work updates fail. They record decision, blocker, next
step and result; they do not rewrite task goals, mark tasks done, deploy software
or grant authority. Semantic `waiting=pending` remains until newer evidence supports `resolved`.
An explicit direct human answer removes the old reminder and exposes
`respondedWaiting.answerState=awaiting_confirmation`, not a claim of successful
login or completed approval. A receipt-only “收到” does not answer a question.
Rearming an answered wait requires a newer result/question/error source; a
summary alone cannot recreate it. Source gaps and stale observations cannot
resolve it.
Formal runtime approvals cannot be cleared by model summaries. An ended/missing
window alone does not resolve waiting. Original source approval/navigation remains
available, and the existing activity area also lists unresolved semantic waits.

## Authenticated local API

All routes reuse the paired Bearer token, Host/Origin checks and no-store headers.
Unimplemented hosts return **501 unsupported_operation**, not an empty success.
Invalid/stale requests return 409; unauthorized requests return 401.

| Operation | Input | Response / effects |
| --- | --- | --- |
| GET `/api/brain/work-state` | `after` integer ≥0, `limit` 1–200 (default 100) | reportOnly, asOf, revision, cursor, hasMore, works, coverage, deliveries, metrics; no source IO/model turn |
| POST `/api/brain/reconcile` | optional `limit` 1–100 (default 32), `after` ≥0, `pageSize` 1–200 | rotating bounded sweep of already registered works, then work-state; no model turn or worker IO |
| GET `/api/brain/observation-source` | exact `work`, `event` | original event excerpt, source IDs/turn/revision, partial flag; no arbitrary path/thread access |
| POST `/api/brain/observations` | thread, turn, requestID, work, revision, sources, summary, waiting | source-linked semantic projection receipt; no task/job/approval mutation |

`works` are revisioned upserts. `active:false` retires an explicitly completed
task; do not infer closure from a missing page. Follow cursor while hasMore, and
use after=0 for a full recovery snapshot. Coverage is global, even when the delta
page is empty: total/current/gaps with lastVerifiedAt. Current requires successful
source reading within 60 seconds. Catching-up, unavailable, ambiguous and stale
coverage must be shown as gaps, not “nothing changed.” Repeated bounded reconcile
calls rotate work fairly; do not claim all fresh until coverage confirms it.

Summary body uses `sources` (1–12 event IDs of that work), `summary` with exactly
`decision`, `blocker`, `next`, `result` (each ≤600 characters), and `waiting` equal
to pending/resolved/unchanged. The exact active Brain thread/turn and current work
revision are required. A repeated identical requestID returns its receipt; changed
content under that ID returns 409. New runtime or human evidence requires a fresh
read before updating. Summary receipts never trigger another observation turn.

`brain_tools.py works [--after CURSOR]`, `source --work WORK --event EVENT` and
`summarize` expose the same contract to the original Brain without putting the
pairing token in argv or model context. Source excerpts may be partial; the
existing original conversation remains the full-history entry point.

## Briefing and cost contract

Integrate with the existing briefing's collection step: reconcile bounded batches,
read all snapshot/delta pages, retain gaps and exact source references, then
present new decisions, added/resolved waits, errors and results. Persist the
consumer cursor only after its own durable report receipt. Do not install another
schedule or dispatch work to compensate for an observation gap. External task
trackers remain under their existing owners.

Only changed relevant works enter an observation turn. The existing context
checkpoint/delta mechanism now includes works and recovers after reconnect,
compaction or a new context boundary. Metrics separate event counts, progress
without model turns, batches/completions/failures/retries, scanned bytes and actual
saved context bytes. Provider token counts are **unavailable/null**, not estimates
or synthetic mock usage billed as real tokens.

The cost probe also feeds the actual notification selector/settling/dedup path
through a synthetic push sender, counting attempts and accepted responses. It
uses temporary keys/subscriptions, never contacts Apple, and cannot establish
device delivery. Keep those counts distinct from model turns and real device
acceptance when measuring a staged installation.

```sh
python -m unittest discover -s tests -p 'test_brain_reporting.py'
python -m unittest discover -s tests -p 'test_observation_http.py'
node tests/test_unified_ui.js
python tests/measure_observation_cost.py
python scripts/test-all.py
# In separate terminals, isolated synthetic provider only:
postman simulation run postman/simulations/brain.sim.yaml --port 4909 --output ndjson --no-history
python tests/verify_brain_http.py
```

These verify local caller behavior and selected real Codex queue operations against
a synthetic SSE provider. They do not establish real-model summary judgment,
production multi-session races, physical iPhone behavior or Windows deployment.
Do not install over an active multi-session service without a coordinated safe
switch and retained rollback bundle/store snapshot.

## Notification policy and diagnostics

Observation turns carry their source event identities into notification selection.
Quiet decision synchronization can update the same visible Brain conversation
without a push. An older unread substantive answer remains eligible when a newer
quiet turn arrives. Native worker and Brain representations share `attentionIDs`;
per-device successful push receipts persist across restarts. Slow polls combine
unread source IDs so another work's later reply cannot hide an earlier result. Source receipts stay local and never enlarge the Web Push body. Failure to deliver
does not consume that receipt. Direct Brain answers keep their existing behavior.
No push receipt is a read receipt or tool approval. Task completion still requires
the existing explicit task action, never merely the end of a worker turn.

`brain_tools` failures include a machine-readable `code`: `local_permission_denied`
(EPERM/EACCES, including wrapped urllib errors), `connection_refused`, `timeout`,
`http_error` (with HTTP status), `configuration_error`, or `connection_error`.
Permission failure does not establish service downtime. No diagnostic retries,
elevates permissions, changes endpoints, or resends uncertain writes.

```sh
python -m unittest discover -s tests -p 'test_notification_noise.py'
python tests/measure_notification_noise.py --source /path/to/baseline-checkout
python tests/measure_notification_noise.py
```

The identical synthetic replay against build 45 and this change measured 25 → 3
model calls and 25 → 3 accepted synthetic pushes: 20 tool-use progress messages,
a short human acknowledgment, a related decision/result, three summary writes,
a new question and another work's failure. Summary writes triggered zero extra
calls in **both** versions; this is not evidence of a historical summary loop.
These are fixture counts, not production token costs or physical phone delivery.
