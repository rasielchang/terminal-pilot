"use strict";
(() => {
  let generation = 0, available = false, busy = false, recording = null, latest = null;
  let summary = null, recoverable = null, replyVersion = 0, awaitingReply = null;
  let audioContext = null, audioBuffer = null, audioSource = null, loadedAudio = "", offset = 0, startedAt = 0;
  let previousAudioSessionType = null, listeningStage = "", demoPlayback = false;
  const maxBytes = 4 * 1024 * 1024;
  function context() {
    const session = current();
    return session && {session: session.id, binding: session.binding, title: session.title, token, generation};
  }
  function matches(capture) {
    const session = current();
    return Boolean(capture && token && capture.token === token && capture.generation === generation &&
      session?.id === capture.session && session.binding === capture.binding && threadVisible() && !document.hidden && $("voice-panel").open);
  }
  function note(text) { $("voice-note").textContent = text; }
  function working() { return ["running", "starting", "waiting"].includes(current()?.phase); }
  function buttons() {
    const session = current(), agent = ["codex", "claude"].includes(session?.tool);
    $("voice-open").hidden = !agent; $("voice-panel").hidden = !agent;
    const active = Boolean(recording);
    $("voice-record").hidden = active;
    $("voice-record").disabled = !available || busy || !connected || sending.has(session?.id) || !session?.canSend || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined";
    $("voice-stop").hidden = !active; $("voice-stop").disabled = recording?.recorder?.state !== "recording";
    $("voice-cancel").hidden = !active;
    $("voice-listen").disabled = !available || busy || active || !latest || working() || Boolean(awaitingReply);
    $("voice-listen").textContent = listeningStage || (audioSource && loadedAudio === summary?.summaryID ? "暫停聆聽" :
      loadedAudio && loadedAudio === summary?.summaryID ? (offset ? "繼續聆聽" : "重播結果") :
      working() || awaitingReply ? "等待本輪完成" : !latest ? "尚無完成回覆" : "聆聽結果");
    $("voice-demo").disabled = !available || busy || active;
    $("voice-use").hidden = !recoverable;
    $("voice-use").disabled = busy || active || !recoverable || !session?.canSend;
    $("voice-playback-stop").hidden = !audioSource;
  }
  function stopAudio() {
    if (audioSource) {
      audioSource.onended = null;
      audioSource.stop(); audioSource.disconnect(); audioSource = null;
    }
    offset = 0;
  }
  function clearAudio() { stopAudio(); audioBuffer = null; loadedAudio = ""; }
  function playbackSession() {
    // WebKit's default Web Audio category is ambient (silenced by iPhone's
    // Ring/Silent switch). This user-requested speech is media playback.
    try {
      if (!navigator.audioSession) return;
      if (previousAudioSessionType === null) previousAudioSessionType = navigator.audioSession.type;
      navigator.audioSession.type = "playback";
    } catch {} // Optional API; AudioContext remains available on older browsers.
  }
  function releaseAudioSession() {
    try {
      if (previousAudioSessionType !== null && navigator.audioSession?.type === "playback") navigator.audioSession.type = previousAudioSessionType;
    } catch {}
    previousAudioSessionType = null;
  }
  function closeAudio() {
    clearAudio();
    demoPlayback = false;
    const old = audioContext; audioContext = null;
    if (old) old.onstatechange = null;
    if (old) old.close().catch(() => {});
    releaseAudioSession();
  }
  // Resume directly in the click handler, before fetching summary/TTS. No silent
  // playback or background keepalive is used to bypass the browser's gesture policy.
  function unlockAudio() {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return Promise.reject(new Error("此瀏覽器無法播放語音，請使用 Safari。"));
    playbackSession();
    if (!audioContext || audioContext.state === "closed") {
      audioContext = new Audio();
      const created = audioContext;
      created.onstatechange = () => {
        if (audioContext !== created || !audioSource || created.state === "running") return;
        pauseAudio(); note("播放被系統中斷，按「繼續聆聽」接著播放。");
      };
    }
    const audio = audioContext;
    let timer;
    return Promise.race([
      audio.resume(),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("音訊未能啟動，請再按一次聆聽。")), 8000); })
    ]).then(() => {
      if (audioContext !== audio || audio.state !== "running") throw new Error("音訊已中斷，請再按一次聆聽。");
      return audio;
    }).catch(error => {
      if (audioContext === audio) closeAudio();
      throw error;
    }).finally(() => clearTimeout(timer));
  }
  function playBuffer() {
    if (!audioBuffer || audioContext?.state !== "running") throw new Error("音訊已中斷，請再按一次聆聽。");
    const position = offset;
    stopAudio(); offset = position;
    const source = audioContext.createBufferSource(); source.buffer = audioBuffer;
    source.connect(audioContext.destination); audioSource = source; startedAt = audioContext.currentTime;
    source.onended = () => {
      if (audioSource !== source) return;
      source.disconnect(); audioSource = null; offset = 0; demoPlayback = false; releaseAudioSession(); note("聆聽完畢，可以直接錄音回覆。"); buttons();
    };
    source.start(0, position); note("正在播放 AI 合成語音。"); buttons();
  }
  function pauseAudio() {
    const position = Math.min(audioBuffer?.duration || 0, offset + audioContext.currentTime - startedAt);
    stopAudio(); offset = position >= (audioBuffer?.duration || 0) ? 0 : position;
    releaseAudioSession();
    note("已暫停，按「繼續聆聽」接著播放。"); buttons();
  }
  async function loadAudio(result, identity, audio, valid) {
    const bytes = Uint8Array.from(atob(result.audio), char => char.charCodeAt(0));
    const decoded = await audio.decodeAudioData(bytes.buffer);
    if (!valid() || audioContext !== audio) return;
    clearAudio(); audioBuffer = decoded; loadedAudio = identity; playBuffer();
  }
  function cancelRecording(message = "") {
    const active = recording; recording = null;
    if (active) {
      active.cancelled = true; clearTimeout(active.timer);
      if (active.recorder?.state !== "inactive") { try { active.recorder?.stop(); } catch {} }
      active.stream?.getTracks().forEach(track => track.stop());
    }
    if (message) note(message);
    buttons();
  }
  function leave() {
    generation++; busy = false; available = false; listeningStage = "";
    cancelRecording(); closeAudio(); latest = null; summary = null; recoverable = null; awaitingReply = null;
    $("voice-review").hidden = true; $("voice-text").textContent = "";
    $("voice-summary-text").textContent = ""; $("voice-status").textContent = "";
    $("voice-panel").open = false; note(""); buttons();
  }
  function suspend() {
    generation++; busy = false; listeningStage = "";
    cancelRecording(); closeAudio(); buttons();
  }
  async function check() {
    const capture = context();
    if (!capture || !$("voice-panel").open) return;
    available = false; buttons(); $("voice-status").textContent = "正在連接語音服務…";
    try {
      const result = await api("/api/voice/status", undefined, {timeoutMs: 80000});
      if (!matches(capture)) return;
      available = result.available;
      $("voice-status").textContent = result.available ? "語音已就緒 · 每次最多 60 秒" : result.error || "語音服務尚未就緒。";
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") note("此瀏覽器無法錄音。請使用 HTTPS Safari，或用 iPhone 鍵盤的麥克風輸入文字。");
    } catch (error) {
      if (matches(capture)) $("voice-status").textContent = error.message;
    }
    finally { if (matches(capture)) buttons(); }
  }
  function encode(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("讀取錄音失敗，請重新錄製。")); reader.readAsDataURL(blob);
    });
  }
  async function transcribe(active) {
    if (!matches(active.capture) || active.cancelled || active.finished) return;
    active.finished = true; recording = null; busy = true; buttons(); note("正在辨識，完成後直接送出…");
    let text = "", deliveryStarted = false;
    try {
      const blob = new Blob(active.chunks, {type: active.recorder.mimeType});
      if (blob.size < 100 || blob.size > maxBytes) throw new Error("錄音太短或過大，請重新錄製。");
      const audio = await encode(blob);
      if (!matches(active.capture)) return;
      const result = await api("/api/voice/transcribe", {
        session: active.capture.session, binding: active.capture.binding, requestID: active.id,
        mime: blob.type, duration: Math.min(65, (Date.now() - active.started) / 1000), audio,
      }, {timeoutMs: 80000});
      if (!matches(active.capture)) return;
      if (result.session !== active.capture.session || result.binding !== active.capture.binding) throw new Error("工作已改變，轉寫結果未送出。");
      text = typeof result.text === "string" ? result.text.trim() : "";
      if (!text || text.length > 8000) throw new Error("沒有辨識到有效內容，或文字超過 8,000 字；尚未送出，請重新錄製。");
      $("voice-review").hidden = false; $("voice-text").textContent = text;
      $("voice-target").textContent = `送往：${active.capture.title}`;
      const previousReply = latest ? {...latest} : {id: null, text: null};
      note("正在送出語音回覆…"); deliveryStarted = true;
      await sendAgentReply(active.capture, text, active.id);
      if (!matches(active.capture)) return;
      awaitingReply = latest && (latest.id !== previousReply.id || latest.text !== previousReply.text) ? null : previousReply;
      summary = null; clearAudio(); $("voice-summary-text").textContent = "";
      note(`已送到 ${active.capture.title} 的 Terminal，等待 Agent 接收。回覆完成後按「聆聽結果」。`);
    } catch (error) {
      if (!matches(active.capture)) return;
      if (text) { recoverable = {...active.capture, text}; $("voice-review").open = true; }
      const failed = error.name === "AbortError" || error instanceof TypeError;
      note(deliveryStarted ? `${failed ? "連線中斷，無法確認是否已送出。" : error.message} 轉寫文字已保留，請查看對話或 Terminal 後再決定是否重送。` :
        failed ? "轉寫連線中斷，尚未送給 Agent；請重新錄製。" : error.message);
    } finally { if (active.capture.generation === generation) { busy = false; buttons(); } }
  }
  async function start() {
    if ($("voice-record").disabled || recording || busy) return;
    const capture = context(); if (!matches(capture)) return;
    closeAudio(); recoverable = null; $("voice-review").hidden = true; $("voice-text").textContent = "";
    const active = {capture, id: requestID(), chunks: [], bytes: 0, cancelled: false};
    recording = active; note("請允許麥克風，準備開始錄音…"); buttons();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}, video: false});
      if (!matches(capture) || active.cancelled) { stream.getTracks().forEach(track => track.stop()); return; }
      active.stream = stream;
      const mime = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"].find(type => MediaRecorder.isTypeSupported(type));
      if (!mime) throw new Error("瀏覽器沒有支援的錄音格式，請改用 Safari 或 Chrome。");
      active.recorder = new MediaRecorder(stream, {mimeType: mime, audioBitsPerSecond: 64000});
      active.recorder.ondataavailable = event => {
        if (active.cancelled || !event.data.size) return;
        active.bytes += event.data.size;
        if (active.bytes > maxBytes) { cancelRecording("錄音過大，已取消。請縮短後再試。"); return; }
        active.chunks.push(event.data);
      };
      active.recorder.onstop = () => {
        clearTimeout(active.timer); stream.getTracks().forEach(track => track.stop());
        if (!active.cancelled) transcribe(active);
      };
      active.recorder.onerror = () => cancelRecording("錄音中斷，請重新錄製。");
      active.recorder.start(500); active.started = Date.now();
      active.timer = setTimeout(() => { if (recording === active && active.recorder.state === "recording") active.recorder.stop(); }, 60000);
      note(`正在錄音給「${capture.title}」。按「說完送出」立即傳送，滿 60 秒也會自動送出。`); buttons();
    } catch (error) {
      if (recording === active) cancelRecording(error.name === "NotAllowedError" ? "麥克風未允許。請在 Safari 網站設定允許麥克風，或用鍵盤語音輸入。" : error.message);
    }
  }
  async function listen(demo = false) {
    if ($(demo ? "voice-demo" : "voice-listen").disabled) return;
    if (!demo && audioSource && loadedAudio === summary?.summaryID) { pauseAudio(); return; }
    const capture = context(), message = latest && {...latest}, version = replyVersion;
    if (!matches(capture)) return;
    demoPlayback = demo;
    const valid = () => matches(capture) && (demo || (!working() && !awaitingReply && version === replyVersion && latest?.id === message?.id && latest?.text === message?.text));
    busy = true; listeningStage = "啟動音訊…"; note("正在啟動音訊…"); buttons();
    try {
      // This call must happen before the first await, while the tap is active.
      const audio = await unlockAudio();
      if (!valid()) return;
      if (!demo && loadedAudio === summary?.summaryID && audioBuffer) { playBuffer(); return; }
      stopAudio(); note(demo ? "正在準備範例語音…" : "正在整理並準備朗讀…");
      listeningStage = demo ? "準備範例…" : "整理摘要…"; buttons();
      if (demo) {
        const result = await api("/api/voice/demo", {}, {timeoutMs: 80000});
        if (valid()) await loadAudio(result, "demo", audio, valid);
        return;
      }
      if (!summary) {
        const result = await api("/api/voice/summary", {session: capture.session, binding: capture.binding, messageID: message.id}, {timeoutMs: 80000});
        if (!valid()) return;
        if (result.session !== capture.session || result.binding !== capture.binding || result.messageID !== message.id) throw new Error("回覆已更新，請再按一次聆聽。");
        summary = result; $("voice-summary-text").textContent = result.text;
      }
      const source = summary;
      listeningStage = "產生語音…"; note("摘要已完成，正在產生朗讀語音…"); buttons();
      const result = await api("/api/voice/speech", {session: source.session, binding: source.binding, summaryID: source.summaryID}, {timeoutMs: 80000});
      if (valid() && summary === source) await loadAudio(result, source.summaryID, audio, valid);
    } catch (error) {
      if (matches(capture)) {
        if (!audioSource) releaseAudioSession();
        note(error.name === "AbortError" ? "語音準備逾時，請再按一次聆聽。" : error.message);
      }
    } finally { if (capture.generation === generation) { busy = false; listeningStage = ""; if (!audioSource) releaseAudioSession(); buttons(); } }
  }
  $("voice-panel").addEventListener("toggle", () => {
    if ($("voice-panel").open) check();
    else suspend();
  });
  $("voice-open").addEventListener("click", () => {
    $("voice-panel").open = true; $("voice-panel").scrollIntoView({block: "start", behavior: "smooth"});
  });
  $("voice-record").addEventListener("click", start);
  $("voice-stop").addEventListener("click", () => { if (recording?.recorder?.state === "recording") recording.recorder.stop(); });
  $("voice-cancel").addEventListener("click", () => cancelRecording("錄音已取消，沒有上傳。"));
  $("voice-use").addEventListener("click", () => {
    if (!recoverable || current()?.id !== recoverable.session || current()?.binding !== recoverable.binding || $("voice-use").disabled) return;
    const previous = $("message").value, next = previous ? `${previous}\n${recoverable.text}` : recoverable.text;
    if (next.length > 8000) { note("加上原有草稿會超過 8,000 字，請先縮短內容。"); return; }
    $("message").value = next; drafts.set(recoverable.session, next); updateComposer();
    recoverable = null; note("已保留到回覆框。請先確認是否送達，再決定是否手動重送。"); buttons();
  });
  $("voice-listen").addEventListener("click", () => listen());
  $("voice-demo").addEventListener("click", () => listen(true));
  $("voice-playback-stop").addEventListener("click", () => { stopAudio(); releaseAudioSession(); note("播放已停止。"); buttons(); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { suspend(); note("已切到背景，錄音與播放已停止；尚未送出的語音已取消。已送出的回覆請查看對話確認。"); }
    else if ($("voice-panel").open) check();
  });
  window.addEventListener("pagehide", leave);
  window.PilotVoice = {
    leave,
    update(session) {
      if (recording && (!session?.canSend || !matches(recording.capture))) cancelRecording("工作狀態已改變，錄音已取消。");
      if (working()) {
        replyVersion++;
        if (!demoPlayback && (summary || audioSource)) { summary = null; clearAudio(); releaseAudioSession(); $("voice-summary-text").textContent = ""; }
        if (session?.phase === "waiting" && $("voice-panel").open) note("Agent 正在等待核准或選項，請查看頁面上方的提示。");
      }
      buttons();
    },
    thread(result) {
      const last = result.messages.at(-1), next = last?.role === "assistant" && last.phase !== "commentary" ? last : null;
      if (latest?.id !== next?.id || latest?.text !== next?.text) {
        replyVersion++; summary = null;
        if (!demoPlayback) { clearAudio(); releaseAudioSession(); }
        $("voice-summary-text").textContent = "";
      }
      if (awaitingReply && next && (awaitingReply.id !== next.id || awaitingReply.text !== next.text)) awaitingReply = null;
      latest = next; buttons();
    }
  };
})();
