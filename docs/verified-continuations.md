# Brain work closeout

The existing logged-in Pilot work conversation is the entry point. The user
gives one instruction in that work. Brain reads the original words and context
to decide whether it is a request to close out, a report only, or a request
that needs more information. Pilot does not interpret the wording.

Ordinary conversation is recorded for synchronization. When the original
instruction requests a report to Brain, the worker uses `report-context` and
`report`, referencing the original work instruction and the saved result
events. The report stays read-only. Worker output, Pilot dispatches, tool
messages and quoted content are evidence. They carry no new user authority.

## One report, one scoped continuation

In the original report turn Brain uses `continuation-plan` with the report ID,
original instruction event, and one of `closeout`, `report_only` or `unclear`.
For closeout, Pilot checks the exact work, session, thread, binding generation,
source revision and current instruction event. It resolves the unique existing
Asana task from the bound work card and checks its existing project and local
month section. It also checks that the original window contains only that
work. Missing or ambiguous context produces a specific pending item.

Brain submits the checked actions using `continuation-propose --reported`.
Pilot records the continuation directly under the existing work's limited
authorization. The report must finish before the independent execution
turn begins. Direct `continuation-propose` continues to support scoped
instructions, including a wait for a future result. Existing login, pairing
and platform approval requirements govern both paths.

The action contract is ordered:

- `verify_delivery` checks the exact source events.
- `asana_complete_existing` completes and moves the exact existing ordinary
  task in its verified project and `YYYY/MM 完成` section, using the existing
  Asana credential. Pilot verifies both changes by readback.
- `close_work` closes the exact idle original window and reads back absence
  for its binding generation.

A later worker input or Brain decision, expiry, changed source or binding,
active work, formal approvals and ambiguous windows can all stop execution.
Asana write intent and readback are journaled per step. Retries read the
effects of uncertain writes before advancing and never resend those writes.
The receipt reports completion only after every action has verified readback.
`report-status` separately records delivery to Brain and the final answer.
`continuation-status` links to the action receipts and any pending reason.

On upgrade, stored authorized continuations keep their existing authority and
receipts. An unfinished proposal without an authorization remains blocked and
can still be read. Retrying the original request returns the existing receipt.

## Verification

The isolated browser fixture exercises the existing work composer, shipped
CLI, authenticated HTTP report, independent continuation and receipt page.
The fixture supplies scripted Brain decisions and synthetic Asana and native
window adapters. Python tests also cover the original failed wording, reversed
order, unconstrained paraphrases, report-only inputs, specialist and tool
sources, formal approval, stale bindings and retry deduplication. A separate
live-model test is needed to validate interpretation.
