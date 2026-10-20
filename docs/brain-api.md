# Brain 協調層契約

## v0.12：主對話與事情

- `topic` 是持續討論的一件事情，與專案路徑、Terminal session 分開。一件事可由多個 session 執行，關閉視窗不刪除事情。空字串代表主對話。
- GET `/api/brain?topic=<id>&before=<messageID>` 回傳該範圍的 `messages`（最新 150 筆，可用 `before` 讀更早）、`hasMore`、`topic`、全部 `topics` 摘要、範圍內 jobs／decisions／working。未知 topic 回 409，不退回主對話。
- POST `/api/brain/topics` 接受 `{requestID,title}`，建立一件事，回傳 `{topic}`；只建討論空間，不啟動模型或 Terminal。
- POST `/api/brain/message` 與 `/api/brain/ack` 增加可省略的 `topic`。送出、重送、回報、已讀都綁定原範圍；主對話已讀不清掉其他事情的未讀。
- message 可帶 `source`（交辦 ID）引用原始回報；必須屬於目前事情（主對話可引用所有交辦）。請求只保存引用 ID，規劃時取原交辦及至多 12,000 字回報、標記 partial，不複製整段 worker 對話。
- 模型契約新增 `topics[{id,title,goal,state,reply}]`（state 為 open/done），dispatch 增加 `topic`。主對話可建立 `new:<key>` 的事情，交易成功後替換為伺服器 UUID；聚焦討論及結果回合只可更新原事情。主對話可統整各件事的簡短摘要，完整對話按範圍提供。
- topic 回覆寫入該事情；重要結果／錯誤／待決事項另在主對話留下短更新與連結，不複製完整內容。一般進度與派工意圖不產生推播。原使用者主對話保留連結，可接著深入任一件事。
- GET `/api/brain/source?job=<id>` 回傳保存的原始回報及精確綁定對話的目前可讀訊息（沿用原讀取器，至多最近 150 則，非完整歷史匯出）；來源已關閉或對話已改變時只顯示保存回報，不讀取替代視窗。閱讀來源不派工、不自動核准。
- 舊訊息留在主對話，舊交辦依原 goal 建立事情並保留回報；遷移不重新執行工作。舊版客戶端省略 topic 時仍使用主對話。
- Brain 仍以一個專用 Codex session 依序協調，各事情／對話／交辦獨立存在 SQLite。不是每件事另開 Terminal，也不宣稱模型上下文完全隔離；後續執行 agent 仍可同時工作。

手機切換事情保留各自草稿、待確認送出與閱讀位置。閱讀原始來源在助理內展開，返回原事情；工作管理仍提供原 session 的直接操作。

## v0.11：持續的 Brain Codex session

- 正式 Brain 後端改用專用 macOS Terminal 中的 Codex TUI／app-server；沿用本機 Codex 模型、工具、登入與權限設定，不再呼叫獨立的 gpt-4.1-mini planner。
- 第一次交代或 POST `/api/brain/open`（requestID）開啟「Brain · 個人助理」，cwd 為 `~/brain`。精確保存 Pilot session、Terminal binding 與 Codex thread ID；關閉後只續接該 thread，不按名稱或資料夾選擇其他對話。
- GET `/api/brain` 增加 `backend`（kind、session、thread、status、detail）。手機可「查看 Brain 對話」，原生工作管理也可叫回該 Terminal。開啟本身不送出模型訊息。
- 經正式 `turn/start` 送入原使用者交代及協調上下文，Codex 保有持續歷史與正常工具能力。最終輸出使用結構化協調契約；手機呈現其中給使用者的回答，Terminal 保留原始輸入、進度與輸出。
- 每個 Brain 回合先持久保存送出意圖與識別碼，再開始 Codex turn。斷線或重啟只觀察原 turn，不盲目重送。模型、權限、sandbox 不由此介面覆寫。
- Brain 自己不列為可派工對象；核准沿用原手機卡片。結果回合不可撤回交辦；使用者回合只可撤回當下可撤回的精確 job。語意驗證仍在送出前執行。
- 若後續規劃失敗，仍展示該次已收到的 agent 原始結果並標示尚未整合；不重跑已完成工作。舊訊息與交辦紀錄保留。

Brain 是 Pilot 的新增服務，舊的 `/api/state`、`/api/thread`、送出、核准、開關視窗與原生介面不改變目標識別規則。
所有 Brain 路由沿用配對 Bearer token、Host／Origin 驗證、JSON、no-store；不提供公開入口。

| 路由 | 輸入 | 結果 |
| --- | --- | --- |
| GET `/api/brain` | 無 | messages、jobs、decisions、paused、working、memory、unreadThrough |
| POST `/api/brain/message` | requestID（1–80）、text（1–8000） | accepted、messageID；先持久保存，背景規劃 |
| POST `/api/brain/control` | requestID、action（pause/resume/takeover）、job（僅 takeover） | ok；pause 阻止新派送，已執行的 agent 繼續 |
| POST `/api/brain/ack` | through（訊息整數 ID） | ok；只確認 Brain 已看過的訊息，不代替核准 |

同一 requestID、同一內容重送取得原結果；不同內容回 409。未知欄位、無效資料與失效目標回 409。
授權失效回 401。GET 不送指令、不清原工作未讀；模型失敗以對話中的錯誤回報，不重送工作。

## 派工、證據與恢復

- Codex 以結構化 `reply`、`dispatches[{session,instruction}]` 與 `release[jobID]` 提交跨工作的協調意圖；
  自身沿用 Codex 工具，跨工作派送仍經 Pilot 精確驗證，不提供自動核准能力。
  release 撤回後續派送，不停止正在執行的 agent；收到使用者新訊息時，先解讀交代，再派送排隊工作。
- 只接受快照中已開啟且可精確綁定的 Codex／Claude 工作；一批最多 4 件，每個使用者目標最多 6 次派送。
  尚未開啟的專案仍從工作管理建立／續接；不根據資料夾猜測 session。
- 派送前再次核對 session、binding、對話 ID 與閒置狀態；忙碌先排隊，waiting 保留原核准要求。
- SQLite 在操作前寫入 dispatching。崩潰後先找帶有唯一交辦 ID 的使用者訊息；找不到時標記結果不明，絕不自動重送。
- 只有該交辦後、下一則使用者訊息前的正式完成回覆，才算該工作的結果。進度不當成果。
  Claude 記錄不標示 final 階段，因此還須收到這次派送後的新完成通知；缺少正式事件時顯示等待完成確認。
  手動插入回覆或按「自己接手」會撤回自動續派權；視窗重綁不自動移轉。
- 模型收到原使用者目標、近期 Brain 對話、精簡的確切工作內容與結果。工作回覆是資料，不能授權新目標。
  回報區分「agent 回報」與獨立驗證，不推論已部署或已驗收。
- Brain 的 SQLite 和讀取個人記憶的設定留在 Pilot Application Support；不寫入既有 Brain Markdown、專案程式或全域 CLI 設定。
  Pilot 預載的記憶摘錄只讀 `~/brain/AGENTS.md`、`projects/brain/operating-rules.md`、`reminders.md`、`projects/*/brief.md`、`projects/*/status.md`（有界限、截斷標記、排除符號連結）；
  Brain Codex 自行使用工具時，則遵守該 session 的工作目錄規則與原權限。
- 切換工作管理不自動中斷已交辦的事；「暫停交辦」停止後續派送。原生視窗管理照常使用。

## 上游與測試界線

正式後端經由本機 Codex app-server 的 `turn/start` 與 `thread/turns/list`，使用同一個 Terminal TUI 的 thread。
不自行讀取 API key、不覆寫模型、核准或 sandbox。Codex 的既有登入與設定負責上游連線。
參照 [Codex app-server](https://learn.chatgpt.com/docs/app-server)。
本機 Codex 0.163.0 的 `thread/items/list` 雖列於 schema 卻回覆未支援，因此只取每頁一個 turn 的 summary，
其中包含 user 與 final message；不載入整段歷史或工具輸出。
測試分成：合成狀態單元測試、真實 Codex（隔離 CODEX_HOME）的協定驗證、Postman SSE 模擬與實際桌面驗證。
`tests/verify_brain_codex_protocol.py` 不呼叫真實模型；`tests/verify_brain_http.py` 需本地 Postman simulation。
模擬與真實模型／Terminal 驗證各自記錄，不能互相替代。
