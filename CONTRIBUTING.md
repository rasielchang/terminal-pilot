# Contributing

歡迎透過 GitHub issue 回報可重現的問題，或提出小範圍的 pull request。
較大的行為／架構變更請先開 issue 討論，並說明實際使用情境。

- 開發環境、建置與驗證命令見 [開發指南](docs/development.md)。
- 修 bug 時補上能重現情境的測試，保留精確 session／視窗綁定、通知競態與重送保護。
- UI 改動同時檢查窄螢幕與桌面。截圖使用合成資料，不放真實對話、專案名稱、配對碼或憑證。
- 不提交 App、個人紀錄、Application Support、agent 歷史、`.env` 或 API key。
- 不降低原 agent 的核准政策，不用聊天文字模擬允許，不在送出結果不明時自動重試。
- 提交前執行 `python3 scripts/check-public.py` 與對應測試。PR 說明問題、改動後行為、實際驗證與未驗證部分。

貢獻會以專案 MIT 授權發布。請只提交有權貢獻的內容，並保留第三方授權聲明。
