# Changelog

## Unreleased — incomplete Terminal window identities

- Terminal may return a window entry with no usable ID. Addressable work now remains visible on Mac/PWA, while the incomplete inventory cannot prove that another work was closed.
- Preserve prior rows during partial reads and retry normally. A later complete read restores reliable closure detection. Local candidate build53; existing notification behavior and session data remain unchanged.

## Unreleased — Terminal window discovery

- Resolve Terminal windows by a captured ID list, so a positional window reference cannot break the entire work list after windows move or close.
- Retry a missing-object snapshot once, and resume normal polling after transient failures. Incomplete reads keep the previous rows and an unknown closure state; Automation denials still require explicit reconnection.
- Local candidate build 52. Existing conversations, jobs, pairings and notification rules are preserved.

## Unreleased — attention contract

- Direct worker discussion, progress, observation scans and summary updates synchronize without starting Brain inference. Delegated results have one durable reporting owner; native approvals/questions do not need model paraphrases.
- Mac and PWA share attention/read receipts across the original work and Brain. Reading clears copies while preserving unresolved cards; late acknowledgements cannot hide newer results.
- Source outages no longer create recovery/gap loops. A sustained outage has one deterministic diagnostic, retired on recovery. Unlinked legacy errors remain diagnostic history.
- Old app-owned queued observation messages are retired through the official queue API when still pending; already-started work and human input are preserved.
- Local candidate build 51; deployment and device acceptance are separate from these source changes. See [contract](docs/attention-contract.md) and [verification](VERIFICATION.md).

## Unreleased — assistant orchestration

- Brain can open a saved project or resume a precisely identified Codex/Claude conversation before dispatching work. Project, matter, conversation and job remain separate.
- Durable launch receipts and existing send receipts prevent duplicate windows or instructions after lost replies. Busy work waits; approvals remain explicit.
- "接回原對話" in assistant job history opens the original conversation without repeating its assignment. The window manager remains available.
- Persistent Brain turns use scoped checkpoints and incremental coordination data. Unrelated memory excerpts no longer invoke skills; explicit user skill requests remain supported.
- This capability requires the macOS native host. The Windows background host still reports unsupported worker-launch operations explicitly.

## 0.13.0 Preview 1

First public preview. macOS 13+ on Apple Silicon.

- Portable Python and Node; no Homebrew or compiler needed for App users.
- Developer ID signed and Apple notarized ZIP for direct installation.
- Native setup for Codex, Claude Code, Tailscale and an optional voice API key.
- Tool discovery on the user's Mac and selectable executable paths.
- Existing project history, pairing and Keychain service retained across updates.
- MIT license, public documentation, contribution/security guidance and CI.
- Original window manager, PWA, attention notifications and experimental Brain retained.
- Closed windows disappear regardless of unread status; long replies open at their beginning.

This is not a claim of broad device compatibility or production readiness.
Check the release's signing/notarization status and [verification](VERIFICATION.md) before distributing binaries.
