# Verified Brain continuations (build 64)

An explicit report remains evidence-only (`reportOnly=true`). A separate,
native scope-confirmed grant can authorize a bounded closeout when that work's
result arrives. Normal direct conversation stays sync-only.

## Source trust and limits

| Source | Evidence | New executable authority |
| --- | --- | --- |
| Codex/Claude transcript `role=user` | Original event in a registered exact work | No: Terminal input, integrations and a local writer are not distinguishable |
| Pilot dispatch rendered as user input | Exact saved dispatch receipt | No |
| Worker text, documents, tool/background notices | Evidence only | No |
| Shared paired HTTP bearer / model-provided confirmation flag | Client access | No |
| Signed macOS native consent RPC, after explicit scope confirmation | Challenge and digest returned over private stdin/stdout | Yes, for the displayed fixed scope only |

There is **no claim that old transcript messages prove physical human origin**.
The service snapshots the full original instruction event, rejects partial or
wrong-work sources, hashes the original plus scope/binding/expiry, and asks the
owner to confirm that exact proposal once. This is a new verified ratification,
not a retroactive promotion of a `user` label. No wording/keyword detector grants
authority. Existing verified standing grants need no second human message when
the result arrives. Legacy rules/docs/AGENTS are not silently imported as grants.

This is an explicit local UI decision, **not cryptographic proof of physical
human presence or macOS account authentication**. No Touch ID or password is
requested. The native reply method is `native_scope_confirmation`; a bearer
client cannot provide that reply through HTTP. Agents must not operate the
confirmation controls on the owner's behalf. Hostile local UI automation and a
compromised signed host, native RPC pipe, or service database/executable are
outside this boundary. It does not rely on a model's claims. A paired
client may propose/query or request the native prompt; it cannot supply a proof.
Only one native prompt may be outstanding. Windows has no verifier and fails
closed; it does not substitute the shared bearer token for native consent.

## Contract

Use the existing installed `brain_tools.py` entry point. `continuation-capabilities`
checks `nativeScopeConfirmation`, `requiresSystemAuthentication` (false), and whether the host credential loader has
provided Asana credentials; it does not reveal or validate a token:

1. `report-context` reads the exact worker environment and source event IDs.
2. `continuation-propose` takes stdin JSON containing `requestID`, `work`,
   `generation`, `instruction`, `sources`, `when` (`now` or `result`),
   `expiresIn` (60–604800 seconds), and `actions`.
3. Open the returned `confirmationURL` on the paired Pilot site. The owner
   selects “在 Mac 確認這次範圍”, reviews the native dialog, then selects
   “同意這次接續”. There is no additional OS authentication step. The specialist
   must not press this on the owner's behalf. No requirement needs to be retyped.
4. `continuation-status --continuation ID` exposes the durable state, authority,
   original event, exact action scopes, receipts, and failure reason.
5. After verified result arrival, the original connected Brain gets one
   `authorized_continuation` turn. `context` exposes its immutable grant and
   evidence. Brain reviews whether the evidence warrants the approved closeout,
   then calls `continuation-run` with stdin `{turn, continuation}`. Generic
   `apply` is still prohibited. Insufficient evidence calls
   `continuation-revoke --continuation ID`, then explains the gap once.

Actions are ordered and fixed:

```json
[
  {"kind":"verify_delivery"},
  {"kind":"asana_complete_existing","task":"1001","project":"1002","section":"1003","month":"2026/11"},
  {"kind":"close_work"}
]
```

Only the first action is required. It validates source identity; it does not
certify business correctness or human acceptance. Brain must review the evidence
before requesting the approved closeout. Asana operates only on an existing
top-level task in the exact project and existing matching completion-month
section. It does not create sections/tasks or change other fields/projects.
Before/after readback is retained. The fixed official API adapter consumes
`ASANA_TOKEN` from the host's existing in-memory credential loader; absent
credentials produce an explicit blocked receipt, never a substitute credential
search or a success claim. Do not pass tokens in tool payloads or argv.

Close checks the original session/thread/generation/binding again, requires an
idle work with no pending job or formal approval, previews the native window,
and refuses a window containing any other work. It never deletes history.
Merge, deployment, deletion, permissions, arbitrary code and formal approval
answers are not supported scopes. Those retain their existing approval paths.

## Ordering, cancellation and persistence

One original instruction owns one proposal, regardless of changed request IDs.
Changing the scope of an existing proposal is rejected. Only verified grants
can queue a continuation. Queue identity is persistent and bound to the original
Brain thread; no new Brain is opened and no other conversation is substituted.
An authorized grant consumes the corresponding delegated job's outcome report,
so the outcome and continuation do not produce two notifications.

Any later potential-human instruction in the worker or Brain, source reset,
binding change, expired grant, explicit revocation, unavailable source or
uncertain window stops the old authority. This is deliberately conservative:
even an unrelated newer human instruction can require a fresh decision. A newer
“stop” or “keep this window” therefore cannot be overridden by old wording or a
worker's summary. Busy/approval-waiting work is not automatically closed out.
Brain pause stays paused.

Each action stores its authority reference and intent **before** external IO,
then records verified completion. Retries return the same completed receipt.
A crash or lost reply leaves an attempted/blocked receipt; this version does
not automatically replay an uncertain write. Restart preserves grants and
receipts, and abandons unfinished consent prompts without granting authority.
`delivered` means an exact Brain turn is known; `summarized` requires the exact
visible final-answer event. Completed API IO alone is not a completed Brain
summary. No observer rescan or summary text can create another grant/turn.

## Verification boundary

`test_brain_continuations.py` and `test_continuation_http.py` exercise source
spoofing, scoped native proof, exact bindings, future-result grants, cancellation,
expiry, approval waits, generic-action escape, durable retries/restart, uncertain
IO, existing-month Asana readback, job coalescing and one final result. The HTTP
test uses the shipped CLI and service. Browser acceptance uses this same route
and the actual confirmation page at phone and desktop sizes.

Repository acceptance work is synthetic. Native UI consent, the model's
reasoning, Asana and window side effects use explicit test adapters in those
fixtures; passing them does **not** claim real user consent, real
Brain inference or a real business update. Actual native consent and
the installed live model need a separately authorized synthetic work acceptance.
Never reopen a closed business task for this test.

## Upgrade from build 63

Existing grants and completed receipts retain their original method, including
`macos_device_owner`, and never replay solely because the app was updated. Only
a newly requested confirmation uses the new native scope-confirmation method.
A declined, interrupted or expired confirmation grants nothing. Removing OS
authentication does not auto-approve a proposal or promote transcript roles.
