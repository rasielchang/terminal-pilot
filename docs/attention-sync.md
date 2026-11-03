# 跨裝置提醒同步

- 原生提醒仍以 `(session, noticeID)` 定位；同一交辦成果以持久 `attentionIDs` 串起原工作、Brain 與推播。Mac 列表、Dock、手機使用同一提醒投影與已讀收據。
- Codex 啟動選單與 Claude 資料夾確認也使用相同的提醒識別，不能只在手機臨時產生另一份紅點。
- `POST /api/ack` 只確認指定的當次提醒；過期的請求不得清除新提醒。成功回覆後，手機立即更新當地角標，後續舊回應不得將同一則提醒重新點亮。
- `/api/state.notifications` 與 `attentionView` 共用狀態。閱讀同一成果的原 Terminal 或 Brain 會同步清除其副本；不同成果不受影響。`attentionView` 必須同時符合 session、binding、noticeID 才能套用。
- Mac 的 `/api/ack` 可帶當次 `attentionIDs`；來源已變更時拒絕舊請求。已讀只停止提醒，不會核准、解除等待或宣告問題解決。原生 IPC 的 `attention` 只同步顯示與收據，不開放新的 HTTP 寫入路由。
- 手機收到推播、返回前景、重新連線或 service worker 更換時，重新核對伺服器狀態，即使未讀集合和上次相同，也必須重設角標。通知與角標的非同步更新依序執行，避免較早的更新最後才完成。
- 不因視窗取得焦點或背景輪詢自動已讀；只在明確查看／標示已處理，以及 agent 正式恢復工作時清除相應提醒。

## iPhone 背景限制

PWA 在背景可能被 iOS 暫停。Web Push 必須顯示通知，不能用不顯示通知的推播只清除圖示角標。因此 Mac 處理完畢、手機 PWA 完全在背景時，不保證角標立即消失；手機重新開啟並成功連線後同步。不能為了清角標多發一則「已讀」通知。

依據：[WebKit Web Push](https://webkit.org/blog/16535/meet-declarative-web-push/)、[Badging API](https://webkit.org/blog/14112/badging-for-home-screen-web-apps/)。
