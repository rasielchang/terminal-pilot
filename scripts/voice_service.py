"""Shared voice operations for the web prototype and future native clients."""
import base64
import binascii
from collections import OrderedDict, deque
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from voice_keychain import read_key

MAX_AUDIO = 4 * 1024 * 1024
MAX_VOICE_BODY = 6 * 1024 * 1024
DEMO_TEXT = "這是 Terminal Pilot 的語音測試。之後會用這樣的方式，簡短告訴你工作結果，以及需要你決定的事情。"


def timeout_stage(stderr):
    stages = {"reading": "讀取語音請求", "connecting": "連線 OpenAI", "sending": "上傳語音請求",
              "waiting": "等待 OpenAI 回應", "receiving": "接收音訊或摘要"}
    latest = "取得 Keychain 金鑰／啟動服務"
    for line in (stderr or b"").decode("utf-8", errors="ignore").splitlines():
        if line.startswith("PILOT_VOICE_STAGE="):
            latest = stages.get(line.partition("=")[2], latest)
    return latest


def run_worker(payload):
    environment = {**os.environ, "OPENAI_API_KEY": read_key()}
    try:
        result = subprocess.run([sys.executable, "-B",
                                 str(Path(__file__).with_name("voice_provider.py"))],
                                input=json.dumps(payload, ensure_ascii=False).encode(),
                                env=environment, capture_output=True, timeout=70)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"語音處理逾時（{timeout_stage(error.stderr)}），請稍後重試。") from None
    if result.returncode:
        raise ValueError("Mac 無法讀取既有 OpenAI Keychain 金鑰，請在電腦檢查語音服務。")
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise ValueError("語音服務回傳格式不正確。") from None
    if not isinstance(value, dict):
        raise ValueError("語音服務回傳格式不正確。")
    if value.get("error"):
        raise ValueError(value["error"])
    return value


class VoiceService:
    def __init__(self, thread, worker=run_worker):
        self.thread = thread
        self.worker = worker
        self.lock = threading.Lock()
        self.inflight = set()
        self.cache = OrderedDict()
        self.summaries = OrderedDict()
        self.attempts = deque()

    def _once(self, key, operation):
        with self.lock:
            now = time.monotonic()
            for old, (created, _) in list(self.cache.items()):
                if now - created > 600:
                    del self.cache[old]
            if key in self.cache:
                return self.cache[key][1]
            if key in self.inflight or len(self.inflight) >= 2:
                raise ValueError("語音服務正在處理，請稍候再試。")
            while self.attempts and now - self.attempts[0] > 60:
                self.attempts.popleft()
            if len(self.attempts) >= 20:
                raise ValueError("語音請求過於頻繁，請一分鐘後再試。")
            self.attempts.append(now)
            self.inflight.add(key)
        try:
            result = operation()
            with self.lock:
                self.cache[key] = (time.monotonic(), result)
                while len(self.cache) > 24:
                    self.cache.popitem(last=False)
            return result
        finally:
            with self.lock:
                self.inflight.discard(key)

    def status(self):
        try:
            return self._once("status", lambda: self.worker({"operation": "status"}))
        except ValueError as error:
            return {"available": False, "error": str(error)}

    def _thread(self, data, *, reply=False):
        identity = data.get("session")
        if not isinstance(identity, str) or not identity or len(identity) > 200:
            raise ValueError("請先選擇 agent 工作。")
        result = self.thread(identity)
        session = result["session"]
        if session.get("tool", "codex") not in {"codex", "claude"}:
            raise ValueError("請選擇 Codex 或 Claude Code 工作。")
        if data.get("binding") != session.get("binding"):
            raise ValueError("工作視窗已改變，請重新選擇後再錄音。")
        if reply and not session.get("canSend"):
            raise ValueError("目前工作不能回覆，請先確認 agent 狀態。")
        return result

    def transcribe(self, data):
        self._thread(data, reply=True)
        identity = data.get("requestID")
        if not isinstance(identity, str) or not 1 <= len(identity) <= 80:
            raise ValueError("缺少語音請求識別碼。")
        duration = data.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 65:
            raise ValueError("每次錄音最多 60 秒。")
        mime = str(data.get("mime", "")).split(";")[0].lower()
        extension = {"audio/mp4": "m4a", "audio/webm": "webm", "audio/wav": "wav",
                     "audio/ogg": "ogg", "audio/mpeg": "mp3"}.get(mime)
        if not extension:
            raise ValueError("不支援這個錄音格式，請使用 Safari 或 Chrome 重試。")
        encoded = data.get("audio")
        if not isinstance(encoded, str) or len(encoded) > MAX_AUDIO * 4 // 3 + 4:
            raise ValueError("錄音過大，請縮短後重試。")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("錄音資料格式不正確。") from None
        if not 100 <= len(raw) <= MAX_AUDIO:
            raise ValueError("錄音太短或過大，請重新錄製。")
        fingerprint = hashlib.sha256(json.dumps([data["session"], data.get("binding"), mime, encoded]).encode()).hexdigest()
        result = self._once("transcribe:" + identity + ":" + fingerprint, lambda: self.worker({
            "operation": "transcribe", "audio": encoded, "mime": mime, "extension": extension,
        }))
        self._thread(data, reply=True)
        return {"text": result["text"], "session": data["session"], "binding": data.get("binding")}

    def _summary_context(self, data):
        result = self._thread(data)
        session, messages = result["session"], result["messages"]
        if session.get("phase") == "waiting":
            raise ValueError("Agent 正在等待核准或選項，請先處理目前提示。")
        if session.get("phase") in {"running", "starting"}:
            raise ValueError("Agent 還在工作，等這輪回覆完成後再產生摘要。")
        if not messages or messages[-1].get("role") != "assistant" or messages[-1].get("phase") == "commentary":
            raise ValueError("尚未取得這輪 agent 回覆，請稍後再試。")
        last = messages[-1]
        if data.get("messageID") != last["id"]:
            raise ValueError("最新回覆已改變，請更新對話後重新產生摘要。")
        text = last["text"]
        if not text.strip() or len(text) > 60000:
            raise ValueError("回覆長度超出摘要範圍，請查看原文。")
        question = next((m["text"] for m in reversed(messages[:-1]) if m["role"] == "user"), "")
        source = {"question": question[-8000:], "reply": text, "status": session.get("status", "")}
        identity = hashlib.sha256(json.dumps([data["session"], session.get("binding"), last["id"], source], ensure_ascii=False).encode()).hexdigest()
        return session, last, source, identity

    def summarize(self, data):
        session, last, source, identity = self._summary_context(data)
        summary = self._once("summary:" + identity, lambda: self.worker({"operation": "summary", "source": source}))
        if self._summary_context(data)[3] != identity:
            raise ValueError("回覆已更新，請重新產生摘要。")
        value = {"summaryID": identity, "text": summary["text"], "session": data["session"],
                 "binding": session.get("binding"), "messageID": last["id"], "title": session.get("title", ""),
                 "source": "latest-reply", "generatedAt": time.time()}
        with self.lock:
            self.summaries[identity] = (time.monotonic(), value)
            while len(self.summaries) > 24:
                self.summaries.popitem(last=False)
        return value

    def speech(self, data):
        identity = data.get("summaryID")
        if not isinstance(identity, str):
            raise ValueError("請先產生口語摘要。")
        with self.lock:
            saved = self.summaries.get(identity)
        if not saved or time.monotonic() - saved[0] > 600:
            raise ValueError("摘要已過期，請重新產生。")
        value = saved[1]
        if data.get("session") != value["session"] or data.get("binding") != value["binding"]:
            raise ValueError("摘要與目前工作不一致，請重新選擇。")
        target = {**data, "messageID": value["messageID"]}
        if self._summary_context(target)[3] != identity:
            raise ValueError("回覆已更新，請重新產生摘要。")
        result = self._once("speech:" + identity, lambda: self.worker({"operation": "speech", "text": value["text"]}))
        if self._summary_context(target)[3] != identity:
            raise ValueError("回覆已更新，請重新產生摘要。")
        return result

    def demo(self):
        return self._once("demo", lambda: {**self.worker({"operation": "speech", "text": DEMO_TEXT}), "text": DEMO_TEXT})
