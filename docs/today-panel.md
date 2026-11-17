# 個人助理今日面板

新的個人助理入口預設為今日面板；上方「聊天」與「今日面板」一鍵往返。Mac 的「今日面板」開啟同一個已配對私人網頁。工作管理、原專員、聊天歷史、附件、核准與既有 work deep link 保持原入口。

資料只來自 Brain 的 `projects/<project>/work-items/*.json`。面板不讀早報快照、不建立另一份進度總帳、不呼叫 Asana；Asana 指派與期限仍由外部正式來源核對，原卡保存引用與查核時間。既有早報索引與聊天整理繼續以原卡為準。

## 原卡的最小安排欄位

以下是虛構示例；不要將私人卡複製進公開 repository。

```json
{
  "title": "核對示範頁面",
  "status": "in_progress",
  "result": "已備妥預覽",
  "next_action": "專員驗證手機版",
  "agenda": {"date": "2030-05-14", "rank": 1, "reason": "本人已安排"},
  "updated_at": "2030-05-14T09:30:00+08:00",
  "source_event": "synthetic-event-id",
  "thread": "synthetic-exact-thread",
  "session": "synthetic-exact-session",
  "due_on": "2030-05-16",
  "asana": {"url": "https://app.asana.com/0/example/example", "verified_on": "2030-05-14"}
}
```

`agenda` 只保存安排日期、排序與原因，不再存一份狀態。缺少 `agenda` 的舊卡保持有效，但不自動排入今天。所有仍有效的未安排卡（包含先前日期）逐項放在「其他追蹤」，不只計數；日切不修改原卡或自動改日期。上方保留今日需要本人與處理中，下方等待／延後包含今日待核對及未安排的工作；今日完成獨立收合。七天內或逾期的明確期限獨立提示，不因此加入今日工作。

狀態採明確 enum，不以文字包含「完成」「核准」等字詞分類。`in_progress/opening/running` 在專員處理中；`needs_user/waiting_on_user` 或明確未解除的 `waiting_on_user` 物件在需要本人；等待、暫緩與未知舊狀態放收合區，未知標待確認。`completed/completed_closed` 只有實際 `completed_at`（兼容 `completed_on/completed_date/completion_date`）屬於今天才進今日完成。檔案修改時間不作完成時間。兼容已結束的 `research_completed`；其他終止狀態不冒充完成。已發布某個階段但仍有後續的卡不等於整件完成。

其他追蹤按結構欄位分組，沒有文字關鍵字推斷：`recurring` 或既有 `notification_policy.frequency=daily/weekly/monthly` 放定期追蹤（不修改通知規則）；延後 enum、`remind_on/start_on`、`scheduling.start_on`、`planned_week.start` 或未來 agenda 放延後／指定日期；明確外部等待 enum 放等待外部；其餘保留為其他待追蹤，未知狀態標待確認。分類顯示數量與短標題，展開後列出所有個別工作，再點標題看最新結果、下一步、來源時間及連結。

排除明確停止／取消／合併、`tracking=excluded_until_user_reopens/stopped/cancelled`（兼容物件 `state`）。沿用正式 `work-state.py` 的舊卡規則：`paused` 及 `tracking_owner=user` 不自行恢復追蹤；若原卡明確設 `tracking=active`，保留為仍有效的暫緩事項。今日已完成即使由本人接管仍歸今日完成。此相容規則不靠文字判斷本人需求，不會為了顯示建立第二套進度。

映射鍵是原卡相對路徑；每張卡恰好落在 `groups` 或 `tracking` 一處。相同專案或 thread 的不同工作不合併。`counts` 的 today/tracking/completed 是互斥計數，期限清單只是引用，不重複計入。Brain 回覆各項有效追蹤應引用該原卡 ID；只有早報引用而尚未有卡的獨立事項，須由正式整理依原來源建立一張卡，保留外部查核時間，不由網頁自動建卡。

有效期限可用 `valid_until` 或 `waiting_on_user.expires_at`；已過期的動作移至等待核對，不持續催辦。

需要本人時，`waiting_on_user` 保存 `action/source_at/release_condition`，可加來源事件陣列。正式核對較新回覆後設為 `null`；這個明確解除優先於舊 status。只有新來源支持時才能重新建立等待。同一精確來源有較新輸入而未正式整理時，面板收起舊催辦、標「原對話有較新回覆，等待正式核對」，不宣稱問題已解決或授予任何操作權限。

## 更新與來源

既有 `summarize` 的原 Brain、active turn、來源 revision、最新事件與 waiting 檢查通過後，會以精確 `work` 或原 thread 對應唯一原卡，更新原卡 result/next_action/updated_at/source_event/waiting_on_user。找不到、對到多卡、已結案、缺 updated_at 無法判定先後或原卡決定比來源新時不覆寫，回傳 `cardSync.state` 供 Brain 核對；不另建卡。摘要不改完成狀態、安排、授權或 Asana。完成、取消、當日安排仍由既有正式整理流程依本人決定更新原卡；`context.source.today` 提供相同即時投影與來源。

原始觀察事件與摘要收據仍是證據紀錄，不成為面板的進度來源。普通更新不新增模型回合或推播；既有重要事件通知規則不變。新回報尚未經正式整理時，面板標記來源已有新資訊，保留最後正式結果。

讀取失敗、失聯、資料超過一天或缺時間均明示。服務記憶體可保留讀取失敗前的最後內容；沒有另寫磁碟總帳。瀏覽器離線保留本頁已讀結果；全新離線啟動不聲稱可讀到歷史快取。卡片以精確 thread/session 對應原專員；無確切連結時顯示與助理核對，不選同資料夾其他工作。

## API 與驗證

`GET /api/brain/today` 與 `GET /api/brain` 的 `today` 欄位提供相同投影：date/readAt/source/groups/tracking/counts/deadlines/notScheduled/errors/revision。沿用 Host、Origin、bearer 配對檢查與 no-store；沒有可公開讀私人卡或修改安排的 API。

```sh
python -m unittest discover -s tests -p test_today.py
python tests/today_fixture.py /tmp/new-empty-today-fixture
# 在另一個 shell，使用已安裝的 Playwright：
node tests/verify_today_ui.cjs /tmp/new-empty-today-fixture /tmp/today-evidence
```

fixture 運行真實 Python HTTP boundary、工作卡 reader 與前端，只替換原生專員與 Brain transcript 來源；不啟動 worker loop、不連真實 Asana／模型／Terminal。涵蓋 390px 與桌面、聊天草稿、精確專員往返、原卡處理中→需本人→完成、離線恢復與 work deep link。Python 覆蓋日切、未安排逐卡映射、同專案／同來源不同工作、去重計數、停止／取消／歷史完成排除、週期分組、較晚回覆解除等待、來源失聯、未知狀態、授權與來源同步。瀏覽器驗收還逐一展開 390px 的所有追蹤卡、核對唯一 ID 與來源入口，並由原 JSON 更新確認項目即時移至今日完成。iPhone 真機的 PWA 重開／推播及使用者的正式核准操作仍需人工驗收。
