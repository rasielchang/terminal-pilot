# 私人 CSV 交付

檔案沿用 Pilot 配對 Bearer 驗證，只能由登入的私人 Pilot 取得；網址本身不授權下載。
不使用照片 API，不建立公開分享網址，不接受 HTTP 檔案路徑。

## Brain 正式入口

使用 Brain 當輪系統契約顯示的 `brain_tools.py` 入口（bundled Python 必須帶 `-B`）：

1. `context` 取得當輪 `contextID`、`turn` 與已綁定 topic/target。
2. `attach` 從 stdin 讀取以下物件。`path` 只在 CLI 本機讀取，HTTP 僅傳內容。
3. 成功回傳 `attachment`（id、name、bytes、sha256、topic、target）與 `link`。
   回覆 Markdown 連結，標籤為「下載 CSV」、網址為回傳的 link；Brain 手機對話也會顯示「已交付檔案」。

```json
{
  "contextID": "本輪 contextID",
  "turn": "本輪 turn",
  "requestID": "export-delivery-unique-id",
  "topic": "原工作 topic",
  "target": "conversation:原工作精確 target",
  "path": "/absolute/private/export.csv",
  "name": "members.csv"
}
```

相同 requestID／內容／工作重試回同一附件；不同內容拒絕。原回合已結束或本人補充指令，先重新 context，保持原工作與附件 requestID；不得冒用舊回合。
只接受 UTF-8（可含 BOM）`.csv` / `text/csv`，1 byte–5 MiB；總附件上限100 MiB。
明確註冊後保存不可變私人副本，不跟隨原檔修改。來源不能是 symlink、device、FIFO。
名稱不含路徑、控制字元或隱藏檔前綴。副本與索引權限600，目錄700。
目前沒有遠端刪除介面；刪除／更換來源檔不會撤銷已註冊副本，配對撤銷仍使舊憑證失效。

## HTTP 契約

- POST `/api/brain/attachments`：`thread,contextID,turn,requestID,topic,target,name,mime,data`（data為base64）。只接受目前 Brain 的原回合及已綁定的工作；reportOnly 不可註冊，須在本人已授權的有效工具回合完成交付。
- GET `/api/attachments?id=…&topic=…&target=…`：只讀索引中的附件；檢查原 Brain、topic、target、大小及SHA-256。未配對401、跨站／跨工作403、無效識別或內容409。
- 成功：原始 bytes、`Content-Type: text/csv; charset=utf-8`、安全的 RFC5987 `Content-Disposition: attachment`、`Cache-Control: no-store`、`nosniff`。不把憑證放入網址，不快取檔案到service worker。

## iPhone

1. 開啟原本已配對的 Pilot PWA／私人 Safari 網址，回到 Brain。
2. 點「取得 檔名.csv」或對話中的「下載 CSV」，等待檔案取得。
3. 點「儲存至檔案／分享」→「儲存到檔案」→選位置；沒有分享按鈕時點「下載 CSV」，再從 Safari 下載項目儲存。

分享按鈕是第二次明確點擊，保留 iOS 要求的使用者手勢。一般瀏覽器使用下載連結。

## 未曾收到交辦的 Claude 空對話

正式 `recovery --topic <topic> --target <target>` 是唯讀診斷；回傳 `historyReadable`、`emptyRecoveryAvailable` 與策略。
有 transcript 時照原 UUID resume；無 transcript 只有以下全部成立才能初始化相同 UUID：
Pilot原先以new開啟的opening已ready、同topic已綁定、原job從未記錄送出、baseline為空、job已撤回／阻擋、
原generation經native證據確認關閉，且Claude metadata只含初始生命週期（eventSequence=3，ended，沒有notice）。
來源缺失本身不足以恢復；有送出／不確定收據、來源讀取錯誤、其他輸入痕跡或關閉不確定都拒絕。

Brain繼續走 `apply` 原 launches/topic/target/instruction；不自行換thread、不透過 `/api/send`。
服務先保存opening與job收據，再使用Claude `--session-id` 相同UUID；native再次檢查原metadata與來源缺失。
開啟後仍核對thread、binding、baseline；不明結果不重開、不重送，已有本人輸入就交回手動。
舊context/turn若已結束，必須重新context後只更新請求的contextID/turn；已完成的其他launch不得再併入重送。
