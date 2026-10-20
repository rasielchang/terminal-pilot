# 發布 Mac 預覽版

公開原始碼與發行可直接安裝的 App 分開處理。此流程不自動建立遠端、推送或公開 release。

## 1. 驗證並匯出來源

```bash
python3 scripts/test-all.py
python3 scripts/export-source.py
```

`dist/open-source/terminal-pilot/` 是可上傳的乾淨來源；旁邊的 ZIP 與 manifest 可供逐檔核對。
使用明確的公開檔案清單，排除本機筆記、建置、設定、對話、配對和金鑰。不要上傳整個工作資料夾。
匯出不是 Git 歷史清洗工具；若自行加入既有 Git 歷史，需另行掃描歷史。

在個人 GitHub 建立空的 `terminal-pilot` repository。先不要勾選自動新增 README／license，因來源已包含。
於匯出目錄初始化 Git，提交後依 GitHub 顯示的遠端指令推送；不要把 `.build` 或 `dist` 加入原始碼提交。
以下在本專案根目錄執行，將 `YOUR_GITHUB_USERNAME` 換成你的個人帳號：

```bash
cd dist/open-source/terminal-pilot
git init -b main
git add .
git commit -m "Initial public preview"
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/terminal-pilot.git
git push -u origin main
```

首次公開前確認作者署名、README、授權與檔案列表，並在 GitHub Security 設定啟用 private vulnerability reporting。
CI 自動測試與建置，但不會自動發布。GitHub 上第一次 CI 成功後再標記 `v0.13.0-preview.1`。

## 2. 可攜 App

```bash
python3 scripts/build.py --portable --output '.build/preview/Terminal Pilot.app'
python3 scripts/verify-portable.py '.build/preview/Terminal Pilot.app'
```

從官方來源下載 `scripts/runtime-lock.json` 鎖定的 Python、Node 和 wheel；每份驗證 SHA-256。
使用 `.build/downloads/` 快取，離線建置可先放入同名原始檔，仍會驗證雜湊。
下載失敗會停止，不會改用本機安裝的 runtime 充當可攜版。
產物保留原 bundle ID 與 Application Support 位置，延續既有資料。

可攜驗證將 App 複製到新路徑、使用空白使用者目錄及最小 PATH，檢查 native library 依賴、
Python imports、HTTPS 根憑證、實際 Node/WebSocket 傳輸與操作後簽章；不呼叫模型或操作真實 Terminal。

## 3. 一般使用者下載：簽章與 Apple 公證

需要 Apple Developer Program 的 **Developer ID Application** 憑證與私鑰。
在 Xcode／Keychain Access 完成帳號和憑證設定，再執行 `security find-identity -v -p codesigning` 確認。
Apple 帳號密碼或 App-specific password 不要放進 repository、命令歷史或 CI 日誌。

先依 [Apple 公證文件](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
用 `xcrun notarytool store-credentials terminal-pilot-notary` 的互動提示建立鑰匙圈 profile。

```bash
python3 scripts/build.py --portable --identity 'Developer ID Application: YOUR NAME (TEAMID)' --output '.build/signed/Terminal Pilot.app'
python3 scripts/package-release.py --app '.build/signed/Terminal Pilot.app' --notary-profile terminal-pilot-notary
```

腳本先驗證可攜性與簽章，提交公證，staple／validate ticket，通過 Gatekeeper 評估後才產生發布 ZIP／DMG 與 SHA256SUMS。
使用 hardened runtime；主程式只宣告 Apple Events 權限，Node 的 JIT 權限只套用於 Node。
未驗證成功會停止，不會把公證失敗的套件標成成功。

## 4. 尚無簽章資格時

原始碼仍可公開。以下只產生清楚標示 `unnotarized` 的本機開發驗證包：

```bash
python3 scripts/package-release.py --unsigned-preview --zip-only
```

它不是一般使用者「下載直接開啟」的驗收結果。不要要求使用者關閉 Gatekeeper 或移除隔離標記。
等憑證可用後重新以正式身分建置、公證，再提供一般使用者安裝版。

## 5. GitHub Release

建立 **Pre-release**，附上通過公證的 DMG／ZIP 與 SHA256SUMS。
Preview 1 可直接使用 [發布說明](release-notes-preview1.md)，列出 Apple Silicon／macOS 13+、選用 CLI、語音計費、實驗性 Brain 和已知限制。
在另一個乾淨帳號／Mac 從 GitHub 下載安裝，驗證 Gatekeeper、首次 Automation、開窗、手機配對與更新保留資料。
截圖只使用合成專案與對話；不要拍到私人工作列表、配對碼或 terminal 內容。
