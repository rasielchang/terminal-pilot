# iPhone／AirPods 遛狗模式

狀態：2026-10-16 v0.8.2 前景網頁語音已改為錄完直接送出、一鍵摘要播放；
自動檢查通過，桌面 Safari 已實際一鍵產生摘要並播放。iPhone 錄音／播放仍待真機驗收。
尚未提供原生 iPhone App 或完成耳機實測。

## 可共用的第一階段：網頁語音原型

已新增 `scripts/voice_service.py` 與 `voice_provider.py`，網頁的 `web/voice.js` 只負責
錄音、自動串接轉寫與送出，以及一鍵摘要播放。之後 Expo 可沿用配對、session binding、轉寫、摘要、TTS 與既有送出 API，
原生端另實作錄音、背景會話、耳機操作與通知，不需要重寫這一組服務。

| 已配對 API | 輸入 | 輸出／用途 |
| --- | --- | --- |
| `GET /api/voice/status` | 無 | Keychain 執行入口是否可取到 key；不代表帳號模型權限或額度已驗證 |
| `POST /api/voice/transcribe` | `session, binding, requestID, mime, duration, audio` | base64 錄音轉為 `text, session, binding`；不呼叫 Terminal send／ack |
| `POST /api/voice/summary` | `session, binding, messageID` | 最新 assistant 回覆的 `summaryID, text, session, binding, messageID, title, source, generatedAt` |
| `POST /api/voice/speech` | `session, binding, summaryID` | 已產生且仍對應最新回覆的摘要音訊，`audio` 為 base64 MP3、`mime` 為 `audio/mpeg` |
| `POST /api/voice/demo` | 空物件 | 固定範例句的音訊，不讀取對話 |

所有路由沿用 Bearer 配對憑證與 Host／Origin 檢查。錄音限 4 MiB，網頁 60 秒自動停止，
API duration 上限 65 秒容納封裝時間；只接受列出的音訊 MIME。上傳 body 上限 6 MiB，
不放寬其他 API 的 40,000-byte 上限。音訊與摘要在記憶體快取最多 10 分鐘／24 筆，
每分鐘最多 20 次未命中快取的語音操作、最多 2 個處理中請求。相同成功請求重用結果，
未完成的重複操作要求稍候；不在服務端自動重試失敗的付費呼叫。

轉寫固定使用 `gpt-4o-mini-transcribe`；摘要使用 `gpt-4.1-mini` Responses、`store: false`；
TTS 使用 `gpt-4o-mini-tts`、`coral`、MP3。選這組先測繁體中文、延遲和口語品質，
不將模型名稱或任意外部 URL 暴露為手機可設定參數。雲端路徑固定 OpenAI 官方 API，
key 由隨附的 Keychain 讀取器在子程序記憶體提供；使用者可在 Mac「環境設定」驗證並儲存自己的 key。
既有 `openai-platform-api` 項目沿用，不依賴外部私人腳本，不回傳 provider 的原始錯誤 body。

摘要僅讀取最後一則 assistant 回覆、它前面的最後一則 user 問題及目前狀態，不送整段歷史、
隱藏推理、Terminal 快照或工具紀錄。明確要求保留未完成／未驗證／未部署與待決選項。
產生前後核對目標與最新內容，agent 恢復或內容改變即拒絕過期結果；TTS 前後也重新核對。
按「說完送出」或滿 60 秒時，自動轉寫並呼叫共用的 `sendAgentReply`，沿用原本送出 API、
精確 session／binding 與併行鎖。這是使用者明確指定的主要流程，不另加確認步驟。
原有打字草稿保留；語音指令不混入草稿。重複停止事件只處理一次，傳送結果不確定時保留轉寫，
不自動重試、不產生另一個 request ID 重送。已送達 Terminal 仍需等待 agent 接收。
「聆聽結果」在同一點擊中先建立／resume Web Audio，再依序取得摘要、TTS、解碼並播放。
可暫停、繼續及重播；新回覆、恢復工作或切換目標後不得播放過期結果，包含解碼期間的變更。
切換、關閉面板、登出、切背景會停止錄音／播放、關閉 AudioContext 並作廢晚到結果。
已送到 Terminal 的回覆不能撤回；已傳出的雲端請求不能保證取消費用。

前景測試仍沿用「主動進入對話標已讀」的現有規則，不新增以開始播放／產生摘要清除提醒的規則。
這一版不自動播報、不接耳機 session 選擇，也不聲稱可以鎖屏遛狗。
先由手機試錄音直接送出、一鍵聆聽、辨識與摘要品質，接著進入下方原生真機里程碑；前景通過不取代背景驗收。

## 已確認的使用情境

使用者戴 AirPods、iPhone 鎖屏放口袋，透過耳機和語音聽取需要處理的工作、選擇
Codex／Claude session、口頭回覆。驗收不以「網頁前景能錄音朗讀」代替。
現有 Mac Terminal 操作、手機網頁、私人 Tailscale HTTPS 入口與專案紀錄繼續沿用。

## 架構方向

- 新增 Expo／React Native iOS 伴隨 App，使用使用者既有的 EAS 雲端建置工作流。
  App 負責背景音訊、耳機輸入輸出、Now Playing、耳機控制和 Siri 入口。
- 優先使用所選 Expo SDK 已支援的音訊能力；耳機 session 切換、Siri 與背景生命週期若超出
  公開 JavaScript API，透過 local Expo module（Swift）及 config plugin 補齊，仍由 EAS 編譯。
  不能只把現有網頁包進 WebView，也不能將 Expo Go 當成這些原生能力的驗收環境。
- 開發使用 development build；遛狗驗收使用已打包 JavaScript、無需 Metro 的實機 preview／
  internal distribution 或 TestFlight build。新增原生模組／權限／音訊設定後需重新建置，
  不能只用 EAS Update 取代。EAS 雲端編譯不改變 iOS 背景執行限制。
- Mac Pilot 保留精確 session／Terminal binding、agent 回覆、工作啟動與通知判斷。
- 對話結果在 Mac 端另產生口語摘要；完整對話不改寫。摘要、語音與送出回覆都以已配對、
  經驗證的 API 取得，不把長對話或音訊直接放進公開推播內容。
- 前景原型先使用上述 OpenAI 模型；原生 App 可沿用或依實測調整。選用雲端服務時需明確標示資料流向與費用；
  憑證留在電腦的既有安全儲存。不能將「經 Tailscale 連線」描述成所有 AI 處理都在本機。
- 原生 iOS 通知需要自己的 APNs 註冊／授權流程；既有 PWA Web Push 訂閱不能直接視為
  原生 App 的推播 token。既有 PWA 訂閱與配對不因新增 App 而撤銷。

## 擬定的耳機與語音操作

以下是待真機驗證的操作映射，不是目前已支援的功能。

| 操作 | 預期結果 |
| --- | --- |
| 啟動「遛狗模式」 | 播報待處理數與目前選定的工作，建立可由使用者結束的語音會話 |
| 耳機播放／暫停 | 播放或暫停目前工作摘要 |
| 耳機下一首／上一首 | 移至下一個／上一個工作，先報名稱、agent 與狀態，再播摘要 |
| 語音「有哪些需要我」 | 讀出待處理工作短清單，允許以序號或名稱選擇 |
| 語音「切到……」 | 解析清單中的工作；同名時口頭列出 agent／簡稱供選擇 |
| 語音「回覆目前工作」 | 先說明接收工作的名稱，收取口述內容，結束錄音即送出 |
| 語音「重播／詳細一點／先略過」 | 重播摘要、播放更多原文，或移至下一項 |
| 語音「停止遛狗模式」 | 結束錄音／語音會話，回到一般待處理通知 |

支援按壓的 AirPods 常以單按、雙按、三按傳出播放／暫停、下一首、上一首；實際取決於型號。
App 接收的是系統媒體命令，不是所有 AirPods 原始手勢。長按可依耳機設定叫 Siri 或切換
聆聽模式，不能承諾直接改成 Pilot 的錄音鍵。點頭／搖頭的 Siri 通知互動也不能當成任意
「選 session／批准 agent」手勢。Siri／App Intents 作為另一路口頭入口，需實測語言、參數、
鎖屏設定與是否要求解鎖；不承諾所有自然語句都能直接觸發。

耳機媒體命令會送給系統目前的 Now Playing App；播放音樂或其他 App 接管後，按鍵未必
仍送給 Pilot。第一版先驗證 Pilot 獨占語音模式，音樂共存列為後續能力。

## 背景執行的核心驗證

使用 AVAudioSession 的播放／錄音類別及正式 background audio 能力。使用者先主動開始
語音會話，首次權限授權在前景完成，再鎖屏。背景能力不等於 App 終止後可無限等待並自行錄音。

需要區分兩種狀態：

1. **正在遛狗對話**：使用者明確開啟的音訊／聆聽會話持續運作，允許耳機／語音控制。
   要驗證數分鐘沒有回覆的空檔、麥克風路由、電量、網路中斷與 Siri／來電介入。
   如果要維持聆聽，必須在產品上清楚告知麥克風何時使用，不能暗中持續上傳環境音。
2. **已停止或被系統中止**：回到通知及使用者透過 Siri／App 主動恢復。通知不是任意背景
   錄音／播放的保證；原生通知播報與語音回覆也需要另外整合並真機驗證。

不以靜音循環、假通話或假定位來維持存活，也不將短時間背景工作當成長時間音訊會話。
若實測發現無音訊空檔後無法直接播新結果，應明確採取「持續聆聽模式」或「通知喚回」的
產品選擇，不能把鎖屏播放一段預先下載的音檔當作完整驗收。

## 口語摘要與送出目標

- 約 15～30 秒，通常 2～3 句：工作名稱、結果／阻礙、需要使用者的下一個決定。
- 只在既有 actionable notice 穩定後排入；中途進度不播，恢復執行或被新通知取代時取消舊排程。
- 重要失敗、未驗證事項與待決問題必須保留，不可為求簡短而將「尚未測試」說成「完成」。
- 確認、拒絕、工具核准等原始選項不得由摘要擅自改寫成可執行命令。一般語音回覆仍走
  現有 send 的前景程序／binding 檢查；現有不支援的權限選單不能藉語音繞過。
- 對話內容與摘要只當資料，不當 Pilot 自身的控制指令。先進入指令／回覆狀態，再解讀語音；
  播放期間避免自己的 TTS 被當成使用者指令，並實測耳機回音與戶外雜音。
- 錄音開始時固定目標 session ID、binding 與待處理 notice ID；新通知排序不能改變送出目標。
  目標關閉或改變時拒絕送出。同名工作先口頭澄清，不依資料夾猜測。
- 錄音前報出目標名稱，結束錄音後直接送出；錄音中可取消。不強制另做口頭確認。網路重試沿用同一 request ID，
  不確定結果時不重複輸入。明確區分「已送到 Terminal」與「agent 已接收」。
- 語音模式擬在整段摘要播放完畢後確認當下 notice；切換、略過或播放中斷不算已讀。
  播放期間到達的新通知仍保留；不能用開始播放就一次清空所有未讀。

## 第一個真機里程碑

先做最小 iOS 音訊測試殼與合成通知來源，確認系統能力，再接完整摘要和多 session 操作。

1. 啟動模式後鎖屏，AirPods 播放／暫停、下一個／上一個均能正確操作三個合成工作。
2. 鎖屏等待 5～10 分鐘，收到一筆模擬完成事件後仍能播報；不能只測持續播放時的背景能力。
3. 全程不拿出手機，選定工作、口述、結束錄音即送出，再聽接收回饋；測試回覆不呼叫真實付費 agent。
4. 新通知插隊時仍送給原先選定工作；同名工作、取消／重錄、斷線重試不會誤送或重送。
5. Siri、來電、耳機取下／重戴、藍牙短暫斷線、Wi-Fi 切行動網路後能恢復或明確報告停止。
6. 最後做一次至少 30 分鐘實際步行，確認背景存活、戶外辨識、音量、電量與操作負擔。

全部通過後才把「放口袋遛狗可用」列為已完成。之後再驗證音樂共存、語音開專案／resume、
原生通知宣告／回覆及 Windows 主機；不讓首輪驗證混入多個平台問題。

目前此 Mac 的 developer directory 為 `/Library/Developer/CommandLineTools`；一般系統／使用者
Applications 目錄與 Spotlight 查詢未找到 Xcode.app。已確認 Node、npm 與 EAS CLI 在 PATH。
使用者已指定 Expo EAS 工作流；**本機 Xcode 不是開始開發或雲端建置的必要條件**，只在
本機 iOS 編譯、模擬器和原生除錯時需要。先前把本機完整 Xcode 寫成必要條件的說法已更正。
EAS 真機建置仍需對應專案、Apple 簽章／裝置或 TestFlight 設定；尚未驗證帳號與簽章，
也沒有建立 EAS 專案、送出雲端建置或產生 iOS 安裝包。
使用者的 AirPods 型號與 iOS 版本待回覆，作為上述測試矩陣的基準。

## 官方依據

- [Web Audio：在使用者手勢內建立／恢復 AudioContext](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Best_practices)

- [OpenAI 音訊轉寫 API](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)
- [OpenAI 語音合成與 AI 聲音揭露](https://developers.openai.com/api/docs/guides/text-to-speech)
- [OpenAI Responses 文字產生](https://developers.openai.com/api/docs/guides/text?api-mode=responses)
- [Expo development builds：EAS 不需要本機原生建置工具](https://docs.expo.dev/develop/development-builds/introduction/)
- [Expo 自訂原生程式碼與 local modules](https://docs.expo.dev/workflow/customizing/)
- [Expo Audio 背景錄音、播放與鎖屏控制](https://docs.expo.dev/versions/latest/sdk/audio/)
- [EAS Build 雲端建置與內部分發](https://docs.expo.dev/build/introduction/)
- [背景播放／錄音音訊會話](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/playandrecord)
- [耳機與系統媒體命令](https://developer.apple.com/documentation/mediaplayer/mpremotecommandcenter)
- [Now Playing 遠端控制資格](https://developer.apple.com/documentation/mediaplayer/remote-command-center-events)
- [AirPods 各型號控制](https://support.apple.com/en-gb/guide/airpods/devb2c431317/26/web/26)
- [Siri 與 App Intents](https://developer.apple.com/documentation/appintents/app-intents)
- [鎖屏 App Intent 驗證政策](https://developer.apple.com/documentation/appintents/intentauthenticationpolicy)
- [Siri 播報通知及回覆](https://support.apple.com/en-mide/102536)
- [iOS 通訊通知整合](https://developer.apple.com/documentation/usernotifications/implementing-communication-notifications)
