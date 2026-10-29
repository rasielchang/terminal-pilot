# 手機啟動確認契約

啟動確認先於 agent 對話／hooks，不能依賴 thread ID 或 hooks 的通知才能顯示。
Mac 從精確綁定的 Terminal 目前畫面辨識 Codex 啟動選單；僅在啟動尚未完成、該 agent 仍為前景程序時啟用。Claude 的資料夾信任沿用既有核准流程。

- `/api/state` 和對話的 session 保留原生提供的 `runtime.approvals`，不以尚未連接的 Codex runtime 覆蓋。
- 啟動卡片 `kind=startup_menu`、`id=startup:<digest>`，含原始畫面、`actions` 及 `actionLabels`。只有已完整辨識的選單才有可操作按鈕。未知格式顯示原始確認與明確說明，不猜測按鍵。
- hooks review 在手機直接展開正式 `hooks/list` 的來源、事件、命令和信任狀態；這是唯讀。信任及不信任由使用者明確選擇，透過原 Codex 選單完成，不使用 bypass 旗標。
- POST `/api/approval` 沿用 `{session,binding,approval,requestID,action,content:{}}`。`startup:` 要求路由到原生 `startupDecision`；不接受瀏覽器提供路徑、PID、按鍵或指令。
- 每次決定重新驗證 session、binding、啟動階段、畫面識別及前景程序。先持久保存送出意圖，再操作；相同 requestID 可重查結果，不重送。選單內容改變後舊卡片失效。
- 一般聊天送出在啟動選單期間禁止。手機核准、工作管理與 Brain 使用同一張卡片；閱讀通知不等同信任。
- 既有 thread 的工具核准仍走正式 app-server 決策通道。此流程不變更 sandbox、工具核准政策或全域信任設定。

驗證涵蓋 hooks／資料夾／更新等編號啟動選單、窄視窗換行、未知格式、舊畫面與錯誤目標、重複送出與重啟、斷線不重試、HTTP 認證及同源限制、手機按鈕與 runtime 合併。
