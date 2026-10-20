# Verification scope

This file contains only public, synthetic-test information. Local acceptance
notes and private project details are excluded from the source distribution.

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
