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

For a new formal report whose **whole instruction** is a supported standalone
closeout request (for example, “跟brain說結案吧”), the service atomically records
an associated continuation with exactly `verify_delivery` then `close_work`.
Quotes, extra clauses, progress-only requests, keep-window and delayed requests
do not use this convenience. Other explicit scopes use `continuation-propose`.
An Asana action is never inferred by the report convenience.

The receipt exposes `continuation`. The report still has `reportOnly=true` and
cannot dispatch, close, release jobs, rewrite authority or answer approvals.
After its exact Brain turn completes, a separate `authorized_continuation` turn
receives the immutable scope and evidence. Brain reviews the delivery, then
uses `continuation-run`; it revokes instead if the evidence is insufficient.
A tool response is not proof of business correctness or human acceptance.

Actions remain limited to:

- `verify_delivery`: verify exact source identity; Brain reviews substantive results.
- `asana_complete_existing`: exact existing task/project/section/month, with readback.
- `close_work`: exact idle session/thread/generation and a window containing only it.

Merge, deployment, payment, data deletion, permissions, arbitrary code, other
work and formal approval answers are not supported. Native approval waiting,
busy sessions and outstanding jobs still prevent closeout. No approval is clicked
on the owner's behalf. The existing Asana adapter and native close checks are
unchanged.

## Ordering, newer decisions and receipts

Any later worker instruction or Brain decision fences the prior grant, including
in-turn steering before transcript flush. Stop, delay or keep-window decisions
therefore win. Expiry, source reset, different thread/generation, ambiguous
windows and a paused Brain also block execution. A report cannot change its
scope on retry. One original instruction owns one report and one continuation.

Intent and authorization are journaled before each external write. Completed
steps return their saved receipts. An uncertain write is never automatically
replayed, including after restart. `completed` requires verified action
receipts; `delivered` requires the exact Brain turn, and `summarized` requires its
visible final-answer event. Neither acceptance nor a missing window is called
closed without the native `closed` receipt.

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
