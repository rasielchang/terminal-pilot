# Terminal Pilot

Windows 原生 PowerShell 背景 Brain 服務的開發方式與限制，見
[Windows 開發指南](docs/windows-development.md)；目前沒有 Windows 桌面版安裝包。

**Keep working in Terminal. See which AI needs you. Pick up from your phone.**

Terminal Pilot 是 macOS 內建 Terminal.app 的工作管理器，也提供私人手機網頁。
集中管理 Codex、Claude Code 與一般 Terminal 視窗；保留熟悉的終端機操作，不需要 tmux。

**目前為 0.13.0 早期預覽版。** 支援 Apple Silicon、macOS 13 以上，介面以繁體中文為主。
Windows 與原生 iPhone App 尚未提供。專案採 [MIT 授權](LICENSE)。

## 可以做什麼

- **需要你才提醒**：完成回覆、等待決定與錯誤排到前面；進度不一直推播。關閉 Terminal 後項目移除。
- **管理工作與專案**：叫回、收起、排列視窗，保存專案，一鍵新開或續接 Codex／Claude 對話。
- **手機接續工作**：透過本地網頁／Tailscale 看對話、回覆、建立資料夾與工作、關閉視窗。
- **核准與通知**：支援受管 Codex 的原始核准卡片、Claude 首次資料夾信任確認，以及 iPhone PWA 推播。
- **Brain 個人助理（實驗）**：用專用 Codex 對話統整不同事情，交辦給已開啟的 agent，原工作管理介面仍保留。
- **語音（選用）**：錄完直接送出；回覆後一鍵聆聽口語摘要。需要自己的 OpenAI API key。

## 安裝 Mac 預覽版

從這個儲存庫的 **Releases** 選擇標示為 Preview 的版本。
0.13.0 Preview 1 的 Apple Silicon 安裝包為 `Terminal-Pilot-0.13.0-preview.1-macos-arm64.zip`，
已完成 Developer ID 簽章與 Apple 公證；一般使用者可以直接安裝，不需要自行建置。
CI artifact 與名稱含 `unnotarized` 的套件僅供開發驗證。

1. 解壓縮 ZIP，把 **Terminal Pilot.app** 拖進「應用程式」。
2. 從「應用程式」開啟，允許 Terminal Pilot 控制 Terminal。既有 Terminal 視窗會自動顯示。
3. 按右下角齒輪或選單 **Terminal Pilot → 環境設定**，檢查想使用的工具。

可攜發布包隨附 Python、Node 與 Python 相依套件，一般使用者不需要編譯或安裝 Homebrew。
Codex、Claude 與 Tailscale 不包含在 App 中，依使用的功能自行安裝及登入：

| 想使用的功能 | 另外需要 |
| --- | --- |
| 一般 Terminal 視窗管理 | 無 |
| Codex 工作、Brain | [Codex CLI](https://developers.openai.com/codex/cli/) |
| Claude 工作 | [Claude Code](https://code.claude.com/docs/en/setup) |
| 外出手機連線、iPhone 推播 | Mac 與手機均安裝並登入 [Tailscale](https://tailscale.com/download) |
| 語音輸入／摘要朗讀 | 在環境設定儲存自己的 OpenAI API key；按 API 用量計費 |

App 會尋找常見的 CLI 安裝位置。找不到時可按「選擇執行檔」，或安裝完成後按「重新檢查」。
第一次使用 agent 的登入或初始化仍可能需要在 Mac 完成。

## 手機怎麼連

Mac 右下角 **網頁 → 啟動網頁**，先在這台 Mac 測試。網頁服務預設只接受本機連線。

外出使用：Mac／iPhone 登入同一個 Tailscale 網路，在 Pilot 開啟 **iPhone 通知（Tailscale HTTPS）**。
用 iPhone Safari 開啟 App 顯示的私人 HTTPS 網址，加入主畫面，再輸入六位數配對碼。
通知需要 iOS 16.4+ 的主畫面 Web App。Mac 必須保持開機、連線且 Pilot 正在執行。

這是私人服務，不需要部署網站。不要將服務直接轉送到公網。完整操作見 [使用指南](docs/user-guide.md)。

## 更新與移除

更新前從選單 **結束 Terminal Pilot（⌘Q）**，等 App 完全退出，再替換應用程式並重新開啟。
關閉管理器視窗只是隱藏；退出 Pilot 不會停止原本的 Terminal 或 agent。
專案、配對與 Brain 歷史保存在使用者的 Application Support，替換 App 會保留這些資料。

移除 App 不會自動刪除專案、agent 對話或鑰匙圈金鑰。資料位置及撤銷配對見 [隱私說明](docs/privacy.md)。

## 預覽版限制

- 僅管理 macOS 內建 Terminal.app；不支援 iTerm2、Windows Terminal 或 Intel Mac 安裝包。
- 一般 Terminal 可查看文字快照；手機尚不提供任意 shell 指令輸入。
- Claude 的一般工具權限、agent 登入與 macOS 系統授權仍需回電腦處理。
- 手機語音限前景；不提供背景錄音、鎖屏對話或 AirPods 切換工作。
- Agent CLI 的協定／歷史格式更新可能影響整合；核准或送出結果不明時，請核對原 Terminal。
- Brain 是實驗功能；跨專案派工請從低風險工作開始，原始 session 仍可直接查看及接手。
- 已做自動測試與本機驗收；更多機型、乾淨 Mac 及 iPhone 真機仍需要社群回饋。

## 開發與參與

[開發指南](docs/development.md) · [發布流程](docs/release.md) · [貢獻方式](CONTRIBUTING.md) · [安全回報](SECURITY.md) · [驗證範圍](VERIFICATION.md)

技術：SwiftUI／AppKit、AppleScript、Python 本地服務、原生 JavaScript PWA。
本專案與 Apple、OpenAI、Anthropic、Tailscale 無隸屬關係；第三方元件授權見 [Third-party notices](THIRD_PARTY_NOTICES.md)。
