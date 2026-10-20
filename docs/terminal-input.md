# 手機的一般 Terminal 輸入

手機可開啟 Terminal，查看目前內容，將文字送到該分頁並按 Return。
這是使用者對目前終端程序的直接輸入，不經 AI 改寫或派送。

- 一般 Terminal 的 `/api/state` session 提供 `canSend=true`；其 `id`、`binding` 仍來自原生視窗快照。
- GET `/api/thread?session=…` 對一般 Terminal 回傳 `source=terminal`、`terminalText`、空 `messages`。內容沿用既有原生快照，限最近 60,000 字元，不寫入新對話檔案。
- POST `/api/terminal/input` 接受且只接受 `{session,binding,requestID,text}`。原生重新驗證工作仍為一般 Terminal，且相同 Terminal 程序、window ID、tty 仍存在；瀏覽器不能提供 AppleScript、路徑、PID 或指令執行器。
- `text` 為 0–8,000 字，允許換行及 tab，禁止其他終端控制字元；空字串表示使用者明確按「Enter」。文字按原樣送入 Terminal，由目前前景程序處理，並追加一次 Return。多行文字可能執行多個指令。
- 此 endpoint 沿用配對 token、Host／Origin 檢查。一般 agent 回覆與核准 endpoint 保持原流程；Brain 和語音不會自動改走 shell 輸入。
- requestID 的精確內容雜湊與送出狀態由原生持久保存，先記錄意圖再送出；相同請求只回傳既有結果。斷線、重開 Pilot 或重複點擊都不自動重送；不同內容使用同一 requestID 回 409。紀錄不保存輸入原文。
- 手機頁面以純文字顯示輸出、維持整頁捲動。只在原本接近底部時跟隨新輸出；切換工作保留草稿，舊回應不得改寫新工作的畫面。

電腦端必須先安裝包含此能力的版本。舊版沒有遠端程式更新入口，不能單靠手機重整，或利用開啟專案的參數來執行更新腳本。
