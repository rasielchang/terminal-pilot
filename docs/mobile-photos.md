# 手機傳照片

Mac 服務 build48 起，個人助理與 Codex／Claude 工作對話的輸入框都有「＋ 照片」。從手機選相簿或檔案，確認縮圖，可移除或重試失敗的上傳；按「送出」才會交給 agent。也可以只送照片。原對話保留照片，點縮圖可放大。

- 每次最多4張、每張原檔12MiB；JPEG／PNG。HEIC 在瀏覽器可解碼時轉 JPEG，否則提示改用 JPEG 或截圖。影片、動畫不支援。
- 服務會修正方向、移除 EXIF，轉成最長邊3072像素的 JPEG；較大圖片降至2048像素，輸出上限4MiB。因此不是原檔備份，細節辨識受縮圖與模型能力限制。
- Codex 經官方 app-server queue 傳 `localImage`。Claude 經精確 Terminal 綁定送出本機圖片讀取要求；Claude 的 Read 工具會收到圖像內容。若原工具需要核准，沿用原核准流程；「已送出」不等於模型已讀取或辨識成功。
- 一般 Terminal 與目前 Windows 背景服務尚不支援照片，後者 API 明確回501。Codex 需原工作提供可用的官方 queue 通道；沒有通道會拒絕送出。
- 切換工作保留各自的暫存照片；重新整理或關閉 PWA 會失去尚未送出的選取，已送出的歷史仍保留。送出結果不明時保留同一識別碼核對，禁止自動重送。
- 可以要求 Brain 把照片交給原工作。正式 `context` 列出可引用的照片 ID，`apply` 的 `dispatches`／`launches` 可附 `photos`。只有既有授權範圍內、精確綁定的目標會取得讀取權；不用再次上傳。

## API 契約

沿用既有配對 Bearer token、Host／Origin 檢查。照片沒有公開靜態網址，讀取回應 `Cache-Control: no-store`。

| 操作 | 請求／結果 |
| --- | --- |
| `POST /api/photos` | `{session,binding,thread,requestID,name,data}`，`data` 為單次上傳的 base64；回 `{photo:{id,name,mime,width,height,bytes,created}}`。相同 requestID、內容與對話回相同收據；不同內容拒絕。 |
| `GET /api/photos` | Query `session,binding,thread,id`；同一 agent home + thread 的授權照片回 JPEG。工作關閉時需精確續接後讀取。 |
| `POST /api/send` | 既有欄位加 `photos:[id]`、`thread`；`text` 可空。上傳不派送；送出與重試共用 requestID，保存 durable queue 收據。 |
| `POST /api/brain/message` | 既有欄位加 `photos:[id]`、精確 Brain `thread`；沿用 Brain durable message 收據。 |
| Brain `apply` | `dispatches`／`launches` 可附當次 schema 允許的 `photos:[id]`；派工 journal 保存來源與目標，精確 resume 完成後才派送。 |

Brain 上傳／讀取使用 `session:"brain"` 與已綁定的 thread，不由客戶端選另一個 Brain。個別工作必須同時符合 session、binding、thread；重用 TTY、不同 agent home、同 thread 多個視窗或未綁定都拒絕。照片傳送中不插入忙碌或等待核准的工作。

401：未配對；403：Host／Origin 不符；409：格式、大小、綁定、工作狀態或收據衝突；501：host 不支援。只有照片上傳端點允許較大 JSON（12MiB 的 base64 加4096 bytes），其他端點仍維持原限制。送出結果不明回 `sent:false,uncertain:true`；只補查 queue／原對話收據，不重發輸入。

## 儲存與驗證

照片存在私人的 Pilot store `photos/`，生成檔名、私有檔案權限、SQLite scope grants 與收據；上限1GiB。目前沒有 UI 清理功能，移除輸入框縮圖不會刪除已上傳的私有檔案。歷史與 context 只放 ID／metadata，增量 context 不重貼 base64；代理本身可能按其既有規則保存圖像。真正送出後，圖像會交給所選 agent／模型供應者處理。

離線回歸：`python -m unittest discover -s tests -p test_photos.py -v`、`node tests/test_photos.js`；完整：`python scripts/test-all.py`。覆蓋登入／來源、格式限制、session 隔離、丟失收據及重啟去重、歷史、Brain 正式轉交／精確續接、context 增量與 UI 草稿隔離。可攜包在隔離使用者下驗證 Pillow 實際圖片解碼。

真模型辨識與瀏覽器合成 UI 檢查需另記錄，不能當作真 iPhone、相簿 HEIC 或目前工程 Terminal 的驗收。最少實機步驟：更新服務並刷新 PWA → 選一張照片、送出請 agent 描述 → 回看縮圖 → 請 Brain 交給指定原工作，確認該工作收到同張圖。核准仍由本人操作。
