# Finite Brain closeout: local-machine trust

## Current policy

The owner uses Pilot on their own computer and accepts the current, exact local
user instruction as the source of finite closeout authority. A new valid
`continuation-propose` no longer requires a second native confirmation dialog.
This policy supports both direct Terminal and Pilot web input. It deliberately
**does not authenticate physical human identity**. A process that can forge or
replace local user transcripts, the service database or executables is outside
this local trust model. Do not describe a `user` label as cryptographic proof.

Worker/assistant output, saved Pilot dispatches, tool/background notifications,
quoted documents presented by a worker, other threads and client-supplied proof
flags still cannot substitute for the exact current instruction event. The
existing provenance, full-event, binding, source-revision and latest-instruction
checks remain. The model must match its proposed scope to the original request;
local trust is not permission to reinterpret a progress request as completion.

New instructions must be dated after this policy first starts and no more than
24 hours old when proposed. The durable policy epoch survives restart. Missing,
old or future-dated source timestamps retain the old confirmation flow. Existing
proposals and report receipts keep their original state; upgrading or retrying
one never promotes it or replays business work.

## Explicit report to independent closeout

Ordinary conversation remains sync-only. Nothing watches for a keyword and
sends a report. Only the specialist's explicit `report` tool creates a formal
report, citing the current original instruction and saved result sources.

A new formal report carries the complete original user event, selected result
events, source revision, exact session, worker thread and binding generation.
The report status initially records `closeout.state=pending`. Brain uses
`continuation-plan` in that exact report turn to record `report_only`, `unclear`,
or to request a `closeout` plan. Brain interprets the whole user message and
reviews the results. Pilot independently checks the original event, freshness,
the live binding, and evidence of a direct closure request. Quoted, negative,
qualified and status-only text does not pass this check. The check is a
conservative eligibility filter. Pilot checks the form of the directive independently of Brain’s semantic
decision. Phrasings outside the supported directive structure remain a report
or a clarification item.

For a closeout, Pilot resolves a unique existing Asana reference from the
canonical work card, via its exact work or thread binding. It accepts project
task URLs, task links with one confirmed existing project, or structured
`asana.task_gid` and `asana.project_gid`. It verifies the top-level ordinary
task and the unique existing `YYYY/MM 完成` section for the host's local month.
Section discovery and movement follow the [Asana sections API](https://developers.asana.com/reference/getsectionsforproject) and [section move API](https://developers.asana.com/reference/addtaskforsection). The adapter uses the existing in-memory `ASANA_TOKEN` integration. A missing
credential, missing or ambiguous task or section, scope spanning projects,
approval task, busy worker or shared window produces a specific pending item.

Brain uses `continuation-propose --reported` with the exact prepared actions.
The report receipt then links to one continuation. The report remains
`reportOnly=true`; its tool contract admits plan and proposal recording.
Generic apply, dispatch, release, close and external writes remain disabled
during the report turn. The independent `authorized_continuation` turn can run
the exact scope after the report is delivered and finishes. Existing direct
continuations retain their existing receipt and confirmation contracts.

Actions remain limited to:

- `verify_delivery`: verify exact source identity; Brain reviews substantive results.
- `asana_complete_existing`: exact existing task/project/section/month, with readback.
- `close_work`: exact idle session/thread/generation and a window containing only it.

Merge, deployment, payment, data deletion, permissions, arbitrary code, other
work and formal approval answers are not supported. Native approval waiting,
busy sessions and outstanding jobs still prevent closeout. No approval is clicked
on the owner's behalf. The report closeout rechecks the Asana target, month section and original
window before writes. Native close rechecks the thread, generation, single
binding and idle phase before invoking the existing closer.

## Ordering, newer decisions and receipts

Any later worker instruction or Brain decision fences the prior grant, including
in-turn steering before transcript flush. Stop, delay or keep-window decisions
therefore win. Expiry, source reset, different thread/generation, ambiguous
windows and a paused Brain also block execution. A report cannot change its
scope on retry. One original instruction owns one report and one continuation.

Pilot journals intent before each write, including the individual Asana move
and completion. A report closeout can resume in up to two separately identified
Brain turns after transient I/O failure. It skips verified steps, reads the
state of uncertain attempts and proceeds to the remaining steps when readback
establishes the effect. An uncertain write is never resent. A definitive
blocker or an unresolved attempt leaves a durable pending reason. The final
window step requires the native exact-generation absence proof, including when
the initial close response was lost.

`completed` requires every action's verified receipt. `delivered` requires the
exact Brain turn and `summarized` requires its visible final-answer event.

`continuation-status` preserves the original instruction, fixed scope, authority
method (`local_user_instruction`, `humanIdentityVerified=false`), action receipts
and failure reason. The receipt page remains available. Its old native consent
button appears only for legacy/unverifiable proposals; new local grants need no
extra click. No browser re-pairing or credential migration is introduced.

## Verification boundaries

The regression tests cover source impersonation through assistant/dispatch/tool
content, exact bindings, old/replayed reports, result ordering, newer decisions,
formal approval waits, report-only and generic-action escape, durable retries,
restart and uncertain writes. Local trust tests explicitly acknowledge that
forging an entire local user transcript is not prevented by this policy.

Synthetic browser acceptance exercises the shipped UI, CLI, HTTP service,
report-only turn, independent continuation and closed receipt. Native window and
model adapters in a synthetic test are not actual human/native-window or live
model acceptance. Report those boundaries separately from installed validation.

## Upgrade from build 63

Existing grants and completed receipts retain their original method, including
`macos_device_owner`, and never replay solely because the app was updated. Only
a newly requested confirmation uses the new native scope-confirmation method.
A declined, interrupted or expired confirmation grants nothing. Removing OS
authentication does not auto-approve a proposal or promote transcript roles.
