# Brain 協調層契約

## 同一 Brain 時間線（Mac host，build 41）

此節取代下方舊版的「topic 各自聊天」與「final JSON 決定可見回覆」契約。背景 Windows host 暫沿用原協調介面；沒有新增 Windows launcher 或部署。

- **唯一聊天來源**是 journal 保存的精確 Codex thread rollout。直接 Terminal 和 web/PWA 的 user、agent commentary/final、公開錯誤事件投影到同一時間線；不顯示 developer context、工具原始輸出、推理或子代理。歷史 planner 回合只投影原本人文字與自然回覆，讀取及重播不會執行動作。
- SQLite 保留 task/job/精確 target/授權 goal/Brain turn、命令 outbox、去重收據、已讀與恢復狀態；舊 messages 表不刪除，也不再作新版可見聊天權威。遷移可回復，原 pairing/store 保留。
- GET `/api/brain?after=<cursor>` 回 `unified=true`、`chatSource=brain-thread`、`thread`、`revision`、`messages`、`cursor/latestCursor`、`hasMore`、`pending`、`activities`、tasks/jobs/decisions。`before` 向前分頁，每頁 100 則。message 的穩定 ID 包含 thread/turn/item；游標為 thread 內 byte offset，**不是 token 或資料庫 ID**。無已知 thread 時為空；讀取失敗保留同步錯誤。客戶端在 thread/revision 改變時重新取首頁，不能合併不同版本。
- `topic` 僅選取任務索引／送出引用，GET 仍保留全局對話。回覆用 links 關聯任務，不搬到另一個聊天。原始工程 GET `/api/thread?session=...&before=...` 或 `after=...` 提供完整可顯示 Codex／Claude 歷史分頁，既有 session/binding 規則不變；開始對話前尚無歷史仍可取得初始化／核准狀態。
- POST `/api/brain/message` 的固定 requestID 先取得 durable `state=queued` 收據。正式 Codex `thread/queue/add` + `thread/queue/start` 依序送入原 thread；後者拒絕 active turn，不能悄悄 steer。與較早 TUI 排隊項目維持順序。遺失回應只用 clientUserMessageId／userMessage.clientId 觀察原輸入；不盲目重送。使用者直接在 TUI 操作仍由 Codex 自身控制，Pilot 不攔截鍵盤。
- 自然回覆不要求 outputSchema，不解析 final JSON 派工。跨工作動作走 `brain_tools.py context|apply`：工具只在記憶體讀既有配對憑證與固定動態 loopback 入口，從 CODEX_THREAD_ID 驗證原 thread；不增加雲端服務或變更原 session 權限。工具命令說明只在合約改版／重連／壓縮後注入，不每輪重貼。

| 路由 | 輸入 | 結果／限制 |
| --- | --- | --- |
| POST `/api/brain/context` | `{thread}` | 必須是原 Brain 的 active turn；回 contextID、turn、scope、sessions、targets、jobs、actionSchema。每次讀取得到新快照；同回合本人追加指令會使舊快照失效。 |
| POST `/api/brain/actions` | `{thread,turn,contextID,requestID,actions}` | actions 為 topics（無 reply）、dispatches、launches、release、可選 close。全批驗證後原子保存收據與 jobs/openings，排程稍後才做 native IO。相同 requestID/內容取原結果，不同內容 409。完成回合／錯 thread／過期綁定不允許重播。 |
| POST `/api/brain/ack` | `{through,activities?,noticeID?,topic?}` | 清理已讀游標及已見活動；只有最後可見回覆的 turn、native notice 均精確相同才同步原生已讀。已讀不等於核准。 |

`brain_tools.py apply` 從 stdin 接收 `{contextID,turn,requestID,actions}`，thread 由原 Codex 環境提供。沒有這些能力的 host 明確回 501，不假成功。所有路由沿用配對、Host/Origin、no-store 與現有正式核准。

正式派工必須經 actions（包括直接 Brain Terminal 的工具派工）；原 `/api/send` 保留本人直接操作語意，不把任意文字誤認成有授權範圍的 Brain job。自然提示明確要求助理不再用 raw send 交辦。完成／失敗／等待／不明結果建立持久回流事件，由 Brain 再整理；主頁同時顯示待整理、錯誤與原工程入口，不能只有 worker 完成通知。指向原工作的查看不撤回追蹤；直接輸入停止舊自動續派，但保留結果觀察。撤回只影響指定 job；同 session 其他事情不被一起取消。

結果只沿用原 task/goal；本人後來改過目標，舊回報不得沿用過期授權續派。相同 session 依 predecessor 排隊；同 checkout 的派送保守串行，不把多視窗當作檔案隔離。close 只對本人交代、精確單一閒置視窗執行，核准／忙碌／多工作視窗不自動關閉；事前寫 journal，不明結果不重試，歷史保留。

Mac 固定 Brain 卡使用 journal thread 定位已存在 Terminal，不靠標題／資料夾；不重複出現在一般列表。關閉後明示並精確續接，不另建聊天頁。web/PWA 回到 Brain 保留草稿、閱讀位置；任務回報不切走目前正在看的工程。

context 不再傳 conversation/memory；當次來源只含必要需求與相關路由／任務增量。新 thread、連線 epoch、壓縮後重建有界 checkpoint；只在原 turn 完成後推進 cursor。測試中的 contextBytes 是送入模型的 UTF-8 bytes，並非真 API token／費用；模擬 usage 不能當節省用量證據。

驗證資產：`test_brain_unified.py`、`test_brain_queue.py`、`test_brain_timeline.py`、`test_unified_ui.js`、HTTP/Swift 回歸與 `verify_brain_http.py`（真隔離 Codex → 本地 Postman SSE）。合成狀態、真協定、Mac 操作與真 iPhone 驗收須分開回報。

## v0.14：開啟／精確續接與增量協調

- 規劃契約新增 `launches[{target,topic,title,instruction}]`。target 只能來自當輪 `targets`：已儲存專案的新對話，或 Pilot 已觀察到的精確 Codex／Claude 對話。空 instruction 只開啟；有內容才建立派工。不能指定任意 shell、路徑、latest 或自行猜對話 ID。
- project（資料夾／儲存專案）、topic（事情）、target（agent 對話）、job（單次交辦）分離。同資料夾的多個對話各有 target；工作 title 不覆寫專案名稱。
- SQLite 先保存 opening 與固定 launchID，再呼叫原生 launch；不明結果只觀察該 launchID，不重開。接上後再核對 tool、directory、精確 thread，才移至原有 queued／dispatch 流程。忙碌等候；核准仍由本人操作；新使用者交代與手動接手會阻止待派送工作。
- POST `/api/brain/work` 接受 `{requestID,job}`，只開啟／續接該 job 的原對話，回 `{opening,session,status}`。不重送交辦、不改核准。相同 requestID 重送回原 opening；GET `/api/brain` 的 `openings` 回報進度與精確 session，前端就緒後直接進工作管理原對話。
- 背景 host 沒有 Terminal launcher 時不提供 launch targets，work 回明確 501。Mac 使用既有原生 launch；不宣稱 Windows 背景服務已有多工作啟動能力。
- 每輪保留當次 userGoal／trigger／scope 及精簡能力清單。移除自動重貼 Brain AGENTS、operating-rules、所有專案記憶；它們由 Brain 本身依任務需要讀取。持續 thread 只傳改變的協調資料，完成後才提交 context cursor；重連、規則版本變更、壓縮或未知歷史回到有界 checkpoint。
- 資料中的 `$skill` 以 JSON Unicode escape 傳遞，避免 Codex 把來源摘錄誤認為當次明確技能叫用；真正使用者當次訊息保留原文字。沒有變更使用者 skill 設定、權限或原 session。

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
- dispatches 接受快照中已開啟且可精確綁定的 Codex／Claude 工作；launches 接受當輪 targets 中的精確對話／已儲存專案；一批最多 4 件，每個使用者目標最多 6 次派送。
  已知原對話由 launches 精確續接；未記錄且無法識別的目標先澄清，不根據資料夾猜測 session。
- 派送前再次核對 session、binding、對話 ID 與閒置狀態；忙碌先排隊，waiting 保留原核准要求。
- SQLite 在操作前寫入 dispatching。崩潰後先找帶有唯一交辦 ID 的使用者訊息；找不到時標記結果不明，絕不自動重送。
- 只有該交辦後、下一則使用者訊息前的正式完成回覆，才算該工作的結果。進度不當成果。
  Claude 記錄不標示 final 階段，因此還須收到這次派送後的新完成通知；缺少正式事件時顯示等待完成確認。
  手動插入回覆或按「自己接手」會撤回自動續派權；視窗重綁不自動移轉。
- 模型收到原使用者目標、近期 Brain 對話、精簡的確切工作內容與結果。工作回覆是資料，不能授權新目標。
  回報區分「agent 回報」與獨立驗證，不推論已部署或已驗收。
- Brain 的 SQLite 和讀取個人記憶的設定留在 Pilot Application Support；不寫入既有 Brain Markdown、專案程式或全域 CLI 設定。
  v0.13 以前 Pilot 預載的記憶摘錄只讀 `~/brain/AGENTS.md`、`projects/brain/operating-rules.md`、`reminders.md`、`projects/*/brief.md`、`projects/*/status.md`（有界限、截斷標記、排除符號連結）；
  v0.14 起取消該預載，避免每輪重貼無關資料及誤觸技能。Brain Codex 自行使用工具時，則遵守該 session 的工作目錄規則與原權限。
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

### Context 恢復與技能邊界

`contextMode=checkpoint` 包含當前事情、至多四則相關近期討論與精簡路由資料；`delta` 的集合欄位是 `{upsert,remove}`，以 `contextBase` 連到上一個已完成回合。SQLite 保存待辦與結果，Codex journal 只在精確 turn 完成後推進 cursor，失敗或 lost response 不提交。

重連建立新的 connection epoch，正式 `item/completed: contextCompaction`（另相容 `thread/compacted`）推進 revision。新 thread、規則 hash 改變、compaction、未知手動 turn 或 connection epoch 改變都重新 checkpoint。不能只靠精簡 turn history 判定 compaction。

來源資料用標準 JSON Unicode escape 避免 `$` 被當成明確技能呼叫；使用者原文另外傳一次，保留當次明確叫用。這沒有停用任何技能，也不變更 session 權限。參考 [Codex skills](https://learn.chatgpt.com/docs/build-skills) 與 [app-server events](https://learn.chatgpt.com/docs/app-server)。

限制：只從 Pilot 保存的 agent 對話 metadata 與目前儲存專案建立 targets；不掃描所有私人對話或猜「最近」對話。路由清單最多 160 筆，優先目前事情與使用者點名的專案，必要時對相關已知對話讀取 400 字提示。未知專案／歧義必須先澄清。Windows 背景 host 不具備原生 worker launcher。
