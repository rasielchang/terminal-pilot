# Terminal Pilot 0.13.0 Preview 1

Terminal Pilot 集中管理 macOS Terminal 裡的 Codex、Claude Code 與一般工作視窗，
在需要你處理時提醒，也能透過私人手機網頁查看對話與回覆。專案以 MIT 授權開源。

這是早期預覽版，歡迎試用並回報問題；尚未完成多機型與乾淨 Mac 的完整驗收。

## 下載與安裝

- 支援 Apple Silicon、macOS 13 以上。
- 下載 `Terminal-Pilot-0.13.0-preview.1-macos-arm64.zip`，解壓縮後把 **Terminal Pilot.app** 拖進「應用程式」。
- 安裝包已完成 **Developer ID 簽章、Apple 公證與本機 Gatekeeper 檢查**，內含 Python／Node，不需自行編譯。
- 校驗碼見 `Terminal-Pilot-0.13.0-preview.1-macos-arm64-SHA256SUMS.txt`。
- 更新前請先 ⌘Q 結束舊版、等待完全退出，再替換 App；既有專案與配對資料保留。

## 主要功能

- 工作視窗與專案管理、Codex／Claude 對話續接、需要處理的工作提醒。
- 手機 PWA 查看與回覆、建立專案資料夾及工作、關閉視窗。
- iPhone PWA 推播、受管 Codex 核准卡片與 Claude 首次資料夾信任確認。
- 實驗性 Brain 個人助理與選用語音輸入、摘要朗讀。

## 使用前須知

- Codex CLI、Claude Code、Tailscale 依功能另行安裝及登入，未包含在 App 中。
- 外出手機連線需要 Mac 保持開機、Pilot 執行中，並與手機連上同一個 Tailscale 網路。
- 語音使用自己的 OpenAI API key，按 API 用量計費；目前限手機前景操作。
- 一般 Terminal 的手機介面提供文字快照，尚不提供任意 shell 指令輸入。
- Claude 一般工具權限、agent 登入與 macOS 系統授權仍需在電腦處理。
- Windows、Intel Mac 安裝包、原生 iPhone App、背景語音與 AirPods 控制尚未提供。
- Brain 仍屬實驗功能；原本的工作視窗管理介面保留，可直接查看及接手各 session。

完整操作與測試範圍請見儲存庫的 README、使用指南與 VERIFICATION.md。
