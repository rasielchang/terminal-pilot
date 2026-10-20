"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../web/voice.js"), "utf8");
const flush = async () => { for (let i = 0; i < 35; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };

function browser() {
  const nodes = new Map(), events = {}, timers = new Map(), calls = [], tracks = [], recorders = [], audio = [], order = [];
  let timerID = 0;
  const session = {id: "one", binding: "tty:1", title: "手機測試", tool: "codex", canSend: true, phase: "completed"};
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {hidden: false, disabled: false, open: false, value: "", textContent: "", handlers: {},
      addEventListener(name, fn) { this.handlers[name] = fn; }, scrollIntoView() {}});
    return nodes.get(id);
  };
  const document = {hidden: false, addEventListener(name, fn) { events[name] = fn; }};
  const stream = () => {
    const track = {stopped: false, stop() { this.stopped = true; }}; tracks.push(track);
    return {getTracks: () => [track]};
  };
  const navigator = {mediaDevices: {getUserMedia: async () => stream()}};
  let audioSessionType = "auto";
  navigator.audioSession = {get type() { return audioSessionType; }, set type(value) { audioSessionType = value; order.push(`session:${value}`); }};
  class Recorder {
    static isTypeSupported(type) { return type === "audio/mp4"; }
    constructor(stream, config) { this.mimeType = config.mimeType; this.state = "inactive"; recorders.push(this); }
    start() { this.state = "recording"; }
    stop() {
      if (this.state === "inactive") return;
      this.state = "inactive";
      this.ondataavailable({data: new Blob(["a".repeat(1000)])}); this.onstop();
    }
  }
  class Reader {
    readAsDataURL() { this.result = "data:audio/mp4;base64," + Buffer.from("a".repeat(1000)).toString("base64"); queueMicrotask(() => this.onload()); }
  }
  const sound = {blocked: false, decode: async () => ({duration: 20})};
  class Audio {
    constructor() { this.state = "suspended"; this.currentTime = 0; this.sources = []; audio.push(this); }
    async resume() { order.push("resume"); if (sound.blocked) throw new Error("音訊未能啟動"); this.state = "running"; }
    async close() { this.state = "closed"; }
    decodeAudioData(bytes) { assert.ok(bytes.byteLength); return sound.decode(); }
    createBufferSource() {
      const node = {connect() {}, disconnect() {}, stop() { this.stopped = true; },
        start(when, offset) { this.offset = offset; this.started = true; order.push("play"); }};
      this.sources.push(node); return node;
    }
  }
  const routes = {
    "/api/voice/status": () => ({available: true}),
    "/api/voice/transcribe": () => ({text: "請先跑測試", session: "one", binding: "tty:1"}),
    "/api/voice/summary": () => ({summaryID: "short-1", messageID: "answer", text: "已修改，尚未驗證。", session: "one", binding: "tty:1"}),
    "/api/voice/speech": () => ({audio: "bXAz", mime: "audio/mpeg"}),
    "/api/voice/demo": () => ({audio: "bXAz", mime: "audio/mpeg"}),
    "/api/send": () => ({sent: true}),
  };
  const api = async (route, data) => { calls.push({route, data}); order.push(route); return routes[route](data); };
  const context = vm.createContext({$, document, navigator, MediaRecorder: Recorder, FileReader: Reader, Blob, Uint8Array, atob,
    window: {AudioContext: Audio, addEventListener(name, fn) { events[name] = fn; }},
    token: "paired", connected: true, sending: new Set(), current: () => session, threadVisible: () => true,
    requestID: () => "record-1", drafts: new Map(), updateComposer() {}, api,
    sendAgentReply: async (target, text, identity) => {
      if (!session.canSend || target.session !== session.id || target.binding !== session.binding) throw new Error("工作狀態已改變");
      return api("/api/send", {session: target.session, binding: target.binding, text, requestID: identity});
    },
    setTimeout: (fn, ms) => { timers.set(++timerID, {fn, ms}); return timerID; }, clearTimeout: id => timers.delete(id),
  });
  vm.runInContext(source, context);
  const app = context.window.PilotVoice;
  app.update(session); app.thread({messages: [{id: "answer", role: "assistant", text: "已修改，但尚未測試。"}]});
  return {node: $, session, app, context, events, navigator, stream, routes, calls, tracks, recorders, document, audio, order, sound, timers,
    click: id => $(id).handlers.click(),
    async open() { $("voice-panel").open = true; $("voice-panel").handlers.toggle(); await flush(); },
    close() { $("voice-panel").open = false; $("voice-panel").handlers.toggle(); },
    async record() { await $("voice-record").handlers.click(); $("voice-stop").handlers.click(); await flush(); }
  };
}
const count = (app, route) => app.calls.filter(call => call.route === route).length;
const played = app => app.audio.flatMap(audio => audio.sources).filter(source => source.started);

(async () => {
  const app = browser(); await app.open();
  app.node("message").value = "既有草稿"; app.context.drafts.set("one", "既有草稿");
  await app.record(); app.click("voice-stop"); app.recorders[0].onstop(); await flush();
  assert.equal(app.node("voice-text").textContent, "請先跑測試");
  assert.equal(app.node("message").value, "既有草稿");
  assert.equal(app.context.drafts.get("one"), "既有草稿");
  assert.equal(count(app, "/api/send"), 1, "Stopping once, double tapping or duplicate onstop must send only once");
  assert.deepEqual(app.calls.find(c => c.route === "/api/send").data,
    {session: "one", binding: "tty:1", text: "請先跑測試", requestID: "record-1"});
  assert.ok(app.tracks.every(track => track.stopped));
  assert.equal(app.node("voice-use").hidden, true);
  assert.equal(app.node("voice-listen").disabled, true, "Must not read the previous answer immediately after sending");
  app.app.thread({messages: [{id: "answer-2", role: "assistant", text: "測試通過"}]});
  assert.equal(app.node("voice-listen").disabled, false);

  const failure = browser(); await failure.open();
  failure.routes["/api/send"] = () => { throw Object.assign(new Error("timeout"), {name: "AbortError"}); };
  await failure.record();
  assert.equal(count(failure, "/api/send"), 1);
  assert.match(failure.node("voice-note").textContent, /無法確認是否已送出/);
  assert.equal(failure.node("voice-review").open, true);
  assert.equal(failure.node("voice-use").hidden, false);
  failure.node("message").value = "保留草稿";
  failure.click("voice-use"); failure.click("voice-use");
  assert.equal(failure.node("message").value, "保留草稿\n請先跑測試");
  assert.equal(count(failure, "/api/send"), 1, "Recovering text must not retry an uncertain send");

  for (const result of [{text: "", session: "one", binding: "tty:1"}, {text: "不可誤送", session: "two", binding: "tty:2"}]) {
    const invalid = browser(); await invalid.open(); invalid.routes["/api/voice/transcribe"] = () => result;
    await invalid.record(); assert.equal(count(invalid, "/api/send"), 0);
  }
  const rejected = browser(); await rejected.open();
  rejected.routes["/api/voice/transcribe"] = () => { throw new Error("轉寫失敗"); };
  await rejected.record(); assert.equal(count(rejected, "/api/send"), 0);

  const cancelled = browser(); await cancelled.open(); await cancelled.click("voice-record");
  cancelled.click("voice-cancel"); await flush();
  assert.ok(cancelled.tracks.every(track => track.stopped));
  assert.equal(count(cancelled, "/api/voice/transcribe"), 0);
  const capped = browser(); await capped.open(); await capped.click("voice-record");
  [...capped.timers.values()].find(timer => timer.ms === 60000).fn(); await flush();
  assert.equal(count(capped, "/api/send"), 1, "The disclosed 60-second limit finishes and sends");

  const late = browser(); await late.open(); const permission = deferred();
  late.navigator.mediaDevices.getUserMedia = () => permission.promise;
  const start = late.click("voice-record"); late.app.leave();
  permission.resolve(late.stream()); await start;
  assert.ok(late.tracks.every(track => track.stopped)); assert.equal(late.recorders.length, 0);

  for (const change of [app => { app.app.leave(); app.session.id = "two"; },
                        app => { app.document.hidden = true; app.events.visibilitychange(); },
                        app => { app.session.binding = "tty:2"; }, app => app.close()]) {
    const switched = browser(); await switched.open(); const transcription = deferred();
    switched.routes["/api/voice/transcribe"] = () => transcription.promise;
    await switched.record(); change(switched);
    transcription.resolve({text: "不可誤送", session: "one", binding: "tty:1"}); await flush();
    assert.equal(count(switched, "/api/send"), 0, "Late transcription cannot send after navigation, background, rebinding or closure");
  }
  const background = browser(); await background.open(); await background.click("voice-record");
  background.document.hidden = true; background.events.visibilitychange(); await flush();
  assert.ok(background.tracks.every(track => track.stopped));
  assert.equal(count(background, "/api/voice/transcribe"), 0);

  const listener = browser(); await listener.open();
  const listening = listener.click("voice-listen");
  assert.equal(listener.order.at(-1), "resume", "Audio must resume in the tap before asynchronous API calls");
  listener.click("voice-listen"); await listening;
  assert.equal(count(listener, "/api/voice/summary"), 1); assert.equal(count(listener, "/api/voice/speech"), 1);
  assert.equal(played(listener).length, 1);
  assert.equal(listener.navigator.audioSession.type, "playback");
  assert.ok(listener.order.indexOf("session:playback") < listener.order.indexOf("resume"), "iPhone media category must be set before resuming audio");
  assert.equal(listener.node("voice-summary-text").textContent, "已修改，尚未驗證。");
  listener.audio[0].currentTime = 5; await listener.click("voice-listen");
  assert.equal(listener.node("voice-listen").textContent, "繼續聆聽");
  assert.equal(played(listener)[0].stopped, true);
  assert.equal(listener.navigator.audioSession.type, "auto", "Pausing releases the playback session");
  await listener.click("voice-listen");
  assert.equal(played(listener).at(-1).offset, 5); assert.equal(count(listener, "/api/voice/speech"), 1);
  played(listener).at(-1).onended(); await listener.click("voice-listen");
  assert.equal(played(listener).at(-1).offset, 0);
  listener.session.phase = "running"; listener.app.update(listener.session);
  assert.equal(listener.node("voice-summary-text").textContent, "");
  assert.equal(played(listener).at(-1).stopped, true);
  assert.equal(listener.node("voice-listen").disabled, true);

  const interrupted = browser(); await interrupted.open(); await interrupted.click("voice-listen");
  interrupted.audio[0].currentTime = 4; interrupted.audio[0].state = "interrupted"; interrupted.audio[0].onstatechange();
  assert.match(interrupted.node("voice-note").textContent, /系統中斷/);
  assert.equal(interrupted.node("voice-listen").textContent, "繼續聆聽");
  await interrupted.click("voice-listen");
  assert.equal(played(interrupted).at(-1).offset, 4);
  assert.equal(count(interrupted, "/api/voice/speech"), 1, "Interrupted audio reuses the prepared speech");

  const stages = browser(); await stages.open(); const stagesSummary = deferred(), stagesSpeech = deferred();
  stages.routes["/api/voice/summary"] = () => stagesSummary.promise;
  stages.routes["/api/voice/speech"] = () => stagesSpeech.promise;
  const stagesListen = stages.click("voice-listen"); await flush();
  assert.equal(stages.node("voice-listen").textContent, "整理摘要…");
  stagesSummary.resolve({summaryID:"short-1",messageID:"answer",text:"摘要",session:"one",binding:"tty:1"}); await flush();
  assert.equal(stages.node("voice-listen").textContent, "產生語音…");
  stagesSpeech.resolve({audio:"bXAz",mime:"audio/mpeg"}); await stagesListen;
  assert.equal(stages.node("voice-listen").textContent, "暫停聆聽");

  const demo = browser(); await demo.open(); const demoSpeech = deferred();
  demo.routes["/api/voice/demo"] = () => demoSpeech.promise;
  const demoListen = demo.click("voice-demo"); await flush();
  demo.session.phase = "running"; demo.app.update(demo.session);
  demo.app.thread({messages:[{id:"progress",role:"assistant",phase:"commentary",text:"還在處理"}]});
  assert.equal(demo.navigator.audioSession.type, "playback");
  demoSpeech.resolve({audio:"bXAz",mime:"audio/mpeg"}); await demoListen;
  demo.app.update(demo.session);
  assert.equal(played(demo)[0].stopped, undefined, "Agent work should not stop the explicit sample playback");
  assert.equal(demo.node("voice-listen").textContent, "等待本輪完成");

  const oldBrowser = browser(); delete oldBrowser.navigator.audioSession; await oldBrowser.open();
  await oldBrowser.click("voice-listen"); assert.equal(played(oldBrowser).length, 1);

  const changed = browser(); await changed.open(); const pendingSummary = deferred();
  changed.routes["/api/voice/summary"] = () => pendingSummary.promise;
  const summarize = changed.click("voice-listen"); await flush();
  changed.app.thread({messages: [{id: "answer", role: "assistant", text: "同 ID 的內容已更新"}]});
  pendingSummary.resolve({summaryID: "old", messageID: "answer", text: "舊摘要", session: "one", binding: "tty:1"}); await summarize;
  assert.equal(changed.node("voice-summary-text").textContent, ""); assert.equal(count(changed, "/api/voice/speech"), 0);

  for (const change of [app => app.close(), app => { app.session.phase = "running"; app.app.update(app.session); },
                        app => { app.session.phase = "waiting"; app.session.canSend = false; app.app.update(app.session); }]) {
    const closed = browser(); await closed.open(); const speech = deferred();
    closed.routes["/api/voice/speech"] = () => speech.promise;
    const speak = closed.click("voice-listen"); await flush(); change(closed);
    speech.resolve({audio: "bXAz", mime: "audio/mpeg"}); await speak;
    assert.equal(played(closed).length, 0);
  }
  const decoding = browser(); await decoding.open(); const decode = deferred(); decoding.sound.decode = () => decode.promise;
  const playback = decoding.click("voice-listen"); await flush();
  decoding.app.thread({messages: [{id: "new", role: "assistant", text: "新回覆"}]});
  decode.resolve({duration: 10}); await playback; assert.equal(played(decoding).length, 0);

  const blocked = browser(); await blocked.open(); blocked.sound.blocked = true;
  await blocked.click("voice-listen"); assert.equal(count(blocked, "/api/voice/summary"), 0);
  assert.equal(blocked.node("voice-listen").disabled, false);
  blocked.sound.blocked = false; await blocked.click("voice-listen"); assert.equal(played(blocked).length, 1);
  blocked.document.hidden = true; blocked.events.visibilitychange();
  assert.ok(blocked.audio.every(audio => audio.state === "closed"));
  const unavailable = browser(); unavailable.routes["/api/voice/status"] = () => ({available: false, error: "Keychain 尚未就緒"});
  await unavailable.open(); assert.equal(unavailable.node("voice-record").disabled, true); assert.equal(unavailable.node("voice-listen").disabled, true);
  console.log("Voice checks passed: stop-and-send once, unchanged drafts, uncertain delivery recovery, target/foreground isolation, 60-second limit, one-tap summary/audio, pause/replay, stale responses and audio interruption.");
})().catch(error => { console.error(error); process.exitCode = 1; });
