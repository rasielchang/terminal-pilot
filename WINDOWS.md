# Windows 原生移植方案

目標已確認：Windows Terminal + PowerShell，Codex / Claude Code 在原生 Windows 執行。
不要求 WSL 或 tmux。這份文件是接下來的實作規格；目前交付的 `.app` 仍是 macOS 版本，
尚未產生或驗證 Windows 安裝包。

## 保留的使用方式

- 電腦繼續使用 Windows Terminal 的鍵盤、捲動、複製貼上。Pilot 管理獨立工作視窗。
- 手機沿用現有 PWA、Tailscale 私人 HTTPS、工作列表、聊天、已讀與推播。
- Codex 與 Claude 各自使用原 CLI 帳號和對話；不改成額外 API 對話，也不複製憑證。
- 主工作停下需要人時才提醒；自動審查、進度、子工作與 idle 重複提醒不推播。
- 關閉工作後仍可由專案紀錄選新對話或 resume；視窗關閉不承諾讓 agent 繼續在背景執行。

## 可沿用與需替換的程式

| 部分 | Windows 做法 |
| --- | --- |
| `web/` | 沿用 PWA、聊天、返回、草稿、已讀與 Web Push；連線文案依主機平台顯示 |
| `web_server.py` | 沿用配對、來源檢查、請求去重、Codex / Claude 對話讀取；換掉 `fcntl`、ifconfig、Unix signal 假設 |
| `web_push.py` | 沿用加密、通知過濾、8 秒確認、撤銷與重試；Windows 資料目錄需套用使用者 ACL |
| `bridge.py` | 保留 hook 事件正規化；替換 TTY、檔案鎖、Unix quoting、exec 啟動和存活判斷 |
| `Sources/Model.swift` | 將排序、專案、已讀、通知 ID 與回覆目標規則移到可跨平台的本地服務 |
| SwiftUI / AppKit | 需要 Windows 桌面外殼與系統匣；可用 .NET / WebView2 重用工作列表 UI |
| `TerminalBridge.swift` | 新增 WindowsTerminalAdapter：獨立命名視窗、HWND 對應、叫回、縮小、排列及受管輸入 |
| Tailscale Serve | 使用 Windows 安裝路徑；保留專用 8443、目標所有權檢查及 localhost 後端 |

## 手機輸入：受管 ConPTY 通道

Windows Terminal 的 `wt -w <name>` 可以指定視窗建立分頁、切換分頁等；
其文件沒有提供等同 Terminal.app `do script ... in tab` 的任意文字輸入 API。
`sendInput` 是 Terminal 的快捷鍵動作，不能將它當作手機服務可安全呼叫的分頁輸入端點。

建議由 Pilot 啟動受管工作時，在 Windows Terminal 內啟動一個輕量 relay：

```text
Windows Terminal 鍵盤 / 顯示
          ↕ 原終端輸入輸出
      Pilot session relay ← 經驗證的本地 IPC ← 手機網頁服務
          ↕ ConPTY pipes
      Codex / Claude Code
```

relay 擁有該工作的 ConPTY handles，將本機鍵盤和手機輸入序列化到同一個 agent；
將 agent 輸出轉回既有 Windows Terminal，並處理尺寸變更。它不提供另一套終端機 UI。
外層仍是使用者的 Windows Terminal，手機聊天仍讀主對話紀錄。
ConPTY 是 Windows 提供的偽終端機機制，不是 tmux。

這是待驗證的設計，不能先保證所有按鍵與貼上已完全無差異。需要實機確認
中文輸入、多行 bracketed paste、Ctrl+C、方向鍵、全螢幕重繪、改變尺寸與退出 agent。
對已經開著且不是由 Pilot 啟動的工作，可先列出與管理視窗；不應宣稱能安全接管其輸入。

## 輸入與狀態的界線

- 回覆只接受 Pilot session UUID + 綁定世代；瀏覽器不能提供 PID、HWND、任意命令或檔案路徑。
- relay 僅允許目前使用者連線的 named pipe，建立時驗證 client/token 與 session。
- agent 結束、視窗關閉或綁定改變立即拒絕舊請求；不得讓延遲回覆落到 PowerShell prompt。
- 一筆 request ID 最多送出一次；不確定是否送達時保留草稿並提示確認，不自動重送。
- hook 和讀取記錄以相同 Windows 使用者的精確 agent session ID 對應，不依 cwd 選最近檔案。
- 文字回覆與權限/選項 UI 分開；一般回覆不會模擬確認鍵或自動同意權限。
- 儲存使用 `%LOCALAPPDATA%\Terminal Pilot`，使用目前使用者 ACL；不依賴 `chmod(0600)`。
- 共用 `list/read/send/ack` 的 JSON pipe 合約；Windows adapter 不改變手機已讀規則。

## 實作與驗收順序

1. Windows 本地服務、使用者資料目錄、hook launcher、精確 session mapping；移植現有合成測試。
2. 單工作 ConPTY relay 與 Windows Terminal 啟動；確認兩種 agent 的本機操作正常。
3. 手機配對、讀取與回覆；驗證中文、多行、斷線、重送、agent 退出與換視窗競態。
4. 桌面列表、排序、叫回、縮小、專案、resume、未讀；保持每工作獨立命名視窗。
5. Tailscale HTTPS、iPhone 原訂閱流程、需要處理才通知；Windows 睡眠/喚醒與重啟恢復。
6. Windows 安裝包及乾淨使用者環境驗收。Mac 上的測試不等於 Windows 實機驗證。

官方資料（2026-10-16 檢查）：

- [Windows Terminal command-line arguments](https://learn.microsoft.com/en-us/windows/terminal/command-line-arguments)
- [Windows Terminal sendInput shortcut](https://learn.microsoft.com/en-us/windows/terminal/tips-and-tricks#send-input-commands-with-a-key-binding)
- [Creating a Pseudoconsole session](https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session)
- [Claude Code 原生 Windows 設定](https://code.claude.com/docs/en/setup#set-up-on-windows)
