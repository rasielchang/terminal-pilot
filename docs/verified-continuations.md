# Finite Brain closeout: local-machine trust

## Current policy

The owner uses Pilot on their own computer and accepts the current, exact local
user instruction as the source of finite closeout authority. A new valid direct, scoped
`continuation-propose` uses the existing local-machine authorization policy.
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
proposals and report receipts never gain authority or replay business work on
upgrade or retry. Pending build 72 report grants without native scope proof stop
with their action receipts preserved.

## Explicit report to independent closeout

Ordinary conversation remains sync-only. Nothing watches for a keyword and
sends a report. Only the specialist's explicit `report` tool creates a formal
report, citing the current original instruction and saved result sources.

A new formal report carries the complete original user event, selected result
events, source revision, exact session, worker thread and binding generation.
The report status initially records `closeout.state=pending`. Brain uses
`continuation-plan` in that exact report turn to record `report_only`, `unclear`,
or to request a `closeout` plan. Brain interprets the whole user message and
reviews the results. Pilot checks provenance of the exact complete user event,
its freshness, and the live binding mechanically. Brain treats a report-only
request, quotation, specialist retelling or negation as read-only. A conditional
request never grants immediate closeout. An old event without recorded user
provenance can still be reported and needs a freshly attributable instruction
before closeout can be proposed.

For a closeout, Pilot resolves a unique existing Asana reference from the
canonical work card, via its exact work or thread binding. It accepts project
task URLs, task links with one confirmed existing project, or structured
`asana.task_gid` and `asana.project_gid`. It verifies the top-level ordinary
task and the unique existing `YYYY/MM 完成` section for the host's local month.
Section discovery and movement follow the [Asana sections API](https://developers.asana.com/reference/getsectionsforproject) and [section move API](https://developers.asana.com/reference/addtaskforsection). The adapter uses the existing in-memory `ASANA_TOKEN` integration. A missing
credential, missing or ambiguous task or section, scope spanning projects,
approval task, busy worker or shared window produces a specific pending item.

Brain uses `continuation-propose --reported` with the exact prepared actions.
The receipt links to a continuation in `needs_confirmation`. Brain gives the
owner the confirmation link after reporting. The existing native scope dialog
displays the original instruction, exact work and the actions to approve.
Only an affirmative response to that dialog creates authority for a report
proposal. The model's interpretation, a request to display the dialog and
client-supplied proof fields carry no execution authority. The report remains
`reportOnly=true` and can record plans and proposals. Generic apply, dispatch,
release, close and external writes remain disabled during the report turn.
The independent `authorized_continuation` turn can execute after the original
report finishes and native scope confirmation succeeds. Direct scoped
continuations retain their existing receipt and authorization contracts.

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
method, action receipts and failure reason. Report proposals use
`native_scope_confirmation`. Direct local grants use `local_user_instruction`
with `humanIdentityVerified=false`. The receipt page presents its consent
button for pending scope confirmations.

## Verification boundaries

The regression tests cover source impersonation through assistant/dispatch/tool
content, exact bindings, old/replayed reports, result ordering, newer decisions,
formal approval waits, report-only and generic-action escape, durable retries,
restart and uncertain writes. Local trust tests explicitly acknowledge that
forging an entire local user transcript is not prevented by this policy.

Synthetic browser acceptance exercises the shipped UI, CLI, HTTP service,
report-only turn, separate scope confirmation, independent continuation and
closed receipt. It also exercises what happens if the model incorrectly proposes
closeout after a negative, quoted, conditional or report-only instruction.
Synthetic Brain decisions and native adapters establish the authority and
execution guarantees under test. Report those boundaries separately from installed validation.

## Upgrade from build 63

Existing grants and completed receipts retain their original method, including
`macos_device_owner`, and never replay solely because the app was updated. Only
a newly requested confirmation uses the new native scope-confirmation method.
A declined, interrupted or expired confirmation grants nothing. Removing OS
authentication does not auto-approve a proposal or promote transcript roles.
