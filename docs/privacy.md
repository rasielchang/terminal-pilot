# 資料與隱私

工作列表與對話讀取在 Mac 本機處理；手機經已配對的本地服務存取。
預設只監聽 localhost。啟用區網或 Tailscale 是使用者明確選擇，不使用 Funnel。

## 儲存位置

`~/Library/Application Support/Terminal Pilot/` 保存專案／顯示設定、通知事件、配對、
推播訂閱與 Brain 的 `brain.sqlite3`。原 agent 的歷史由 Codex／Claude 自行管理。
`tools.json` 只保存 CLI 路徑，不含憑證。App 更新不會清空這個資料夾。

語音 API key 存在 macOS Keychain 的 `openai-platform-api` service。
新設定使用目前使用者 account；已有同 service 項目則保留原 account。
金鑰不回傳手機、不寫進專案、命令參數或診斷日誌。

## 什麼會離開 Mac

- 手機閱讀／回覆：透過配對連線傳送所選 session 資料。
- 語音轉錄：上傳所選錄音至 OpenAI；摘要上傳最後一則 agent 回覆、對應問題與狀態；朗讀上傳摘要。
  由使用者 API 帳號計費。錄音與音訊只在記憶體處理。
- iPhone 推播：工作名稱與狀態經加密 Web Push 送至 Apple；不包含對話全文、資料夾或配對憑證。
- Brain 與其他 agent：指令及必要上下文交給已登入的 Codex／Claude，由對應供應商處理。

Pilot 沒有產品分析／廣告 SDK，不向維護者傳送使用紀錄。工具安裝、登入及各供應商資料政策由該工具負責。

## 撤銷與移除

Mac「網頁 → 重設配對」撤銷所有既有連線與推播訂閱；手機登出撤銷該裝置的儲存配對。
退出 Pilot 停止網頁服務；原 Terminal 繼續。移除 App 不會刪除資料。
要完整清除，先退出 Pilot，再自行刪除上述 Application Support 資料夾，以及不再使用的鑰匙圈金鑰。
同 service 金鑰可能也被其他本機工具使用，刪除前請確認。原 agent 歷史不會因此被刪除。
