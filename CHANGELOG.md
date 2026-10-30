# Changelog

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
