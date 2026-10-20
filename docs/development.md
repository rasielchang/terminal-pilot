# Development

Apple Silicon Mac、macOS 13+、Apple Command Line Tools、Python 3.13 及 Node 22+。
一般使用者請安裝 Releases 的 Mac App；此文件給修改原始碼的人。

```bash
xcode-select --install
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/build.py --output '.build/dev/Terminal Pilot.app'
open '.build/dev/Terminal Pilot.app'
```

開發版使用這個 venv 的 Python，請保留 venv。可攜包使用 `--portable`，不記錄開發者路徑。
`Sources/` 是 Swift App；`scripts/` 是本地服務；`web/` 是不用前端框架的 PWA。

## Tests

```bash
python scripts/test-all.py
```

執行 Python、Swift model 和全部 JavaScript 測試，預設只用合成資料與假 agent，不產生模型費用。
Swift tests 不操作真實 Terminal。Automation、Keychain 首次授權、iPhone 麥克風與推播需人工驗收。

```bash
# Optional: real Codex protocol, isolated temporary home; no model inference
PILOT_CODEX_PROTOCOL_TEST=1 python -m unittest discover -s tests -p test_codex_protocol.py
```

## Restart without losing work

先 ⌘Q 正常退出管理器，確認程序已結束，再替換 App、驗證簽章並啟動一次。
關閉視窗只是隱藏；不要連續 quit/open，也不要強制停止使用者的 Terminal 或 agent。
`TERMINAL_PILOT_STORE` 可指向暫存資料夾，但不改變 Terminal Automation 的操作對象。

[Brain](brain-api.md) · [Claude trust](claude-workspace-trust.md) · [Mobile directories](mobile-directories.md)

不要變更 Mac 現有 Application Support 或 agent 歷史來準備公開版。
用 `scripts/export-source.py` 匯出乾淨來源；個人筆記留在 `.local/`。
