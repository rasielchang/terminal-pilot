# Changelog

## Unreleased — external Terminal closure

- Reconcile manually closed windows and individual tabs with exact target reads even when an unrelated Terminal item makes the global inventory partial. Unavailable target evidence retains the work as unverified.
- Reconcile persisted jobs after restart using the original binding generation. Confirmed closure stops unfinished jobs and releases the shared checkout; it never completes a task or resumes it in another conversation.
- Keep original transcripts, results and job history. The Mac and web work lists remove closed tabs, and observed runtime status becomes ended after exact closure proof.
- Candidate build 57; live manual-close acceptance is separate from automated regression checks.

## Preview build 55

- 個人助理只在輸入區顯示目前有效的簡短動作；過期、已回答、待核對及重複紀錄收進可展開歷史，保留精確原工作入口，不把已讀當成完成。
- 工作來源讀取與視窗可操作性分開判斷；部分視窗清單異常不再讓正常工作全部顯示來源缺口，context 列出未納入 session 的原因。
- 照片與派工失敗保留安全診斷。確認寫入前的連線未就緒可用同一收據短暫等待；超時明示阻塞，可能已送出時只查收據，不重送或重開視窗。

## Preview build 54

- 個人助理新工作預設新對話；正式派工拒絕把新事情送入舊工作。同一事情仍可精確續接，建立失敗不改用舊對話備援。
- 已知工作目錄提供新對話入口；修正空 thread 被誤判為視窗衝突，並驗證新建未接回已知舊對話。
- 視窗清單逐列確認可用性。無有效 ID 的項目隔離並保存精簡診斷；已確認工作繼續使用，未確認舊列明示狀態並暫停視窗操作。

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
