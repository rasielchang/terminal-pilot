"use strict";
const $ = id => document.getElementById(id);
let token = sessionStorage.getItem("pilot-token") || "";
const persistentPairing = location.protocol === "https:";
try { if (persistentPairing) token = localStorage.getItem("pilot-token") || token; } catch {}
function rememberToken() {
  sessionStorage.setItem("pilot-token", token);
  try { if (persistentPairing) localStorage.setItem("pilot-token", token); } catch {}
}
let incomingWork = new URLSearchParams(location.search || "").get("work") || "";
const fragment = new URLSearchParams(location.hash.slice(1));
if (history.state?.pilot?.view !== "thread" || history.state.pilot.session !== fragment.get("work")) incomingWork ||= fragment.get("work") || "";
if (fragment.has("token")) {
  token = fragment.get("token");
  rememberToken();
  history.replaceState(null, "", location.pathname);
}
let sessions = [], selected = "", needsOnly = false, connected = false, polling = false;
const progressMessages = new Map();
const progressStarts = new Map();
let messageKey = "", sessionListKey = "", selectionGeneration = 0;
const threadPages = new Map(), threadPositions = new Map();
let loadingThreadEarlier = false;
const drafts = new Map(), feedback = new Map(), sending = new Set(), userMessages = new Map();
const acknowledgedNotices = new Map();
let notificationSessions = null;
let viewGeneration = 0;
let pendingLatest = null;
const mobileLayout = window.matchMedia("(max-width: 720px)");
let listScrollTop = history.state?.pilot?.listScrollTop || 0, threadScrollTop = 0;
let restoreRoute = true, returningToList = false;
history.scrollRestoration = "manual";
if (!["list", "thread"].includes(history.state?.pilot?.view)) replaceListRoute();

function replaceListRoute() {
  const mode = window.PilotAssistant?.mode();
  const topic = window.PilotAssistant?.topic?.() ?? fragment.get('topic') ?? '';
  history.replaceState({pilot: {view: "list", listScrollTop, ...(mode ? {mode} : {}), ...(topic ? {topic} : {})}}, "", `${location.pathname}${mode ? `#view=${mode}${mode === 'assistant' && topic ? `&topic=${encodeURIComponent(topic)}` : ''}` : ""}`);
}
function recordThreadRoute(id) {
  const mode = window.PilotAssistant?.mode();
  const state = {pilot: {view: "thread", session: id, listScrollTop, ...(mode ? {mode} : {})}};
  const url = `${location.pathname}#${mode ? `view=${mode}&` : ""}work=${encodeURIComponent(id)}`;
  if (history.state?.pilot?.view === "thread") history.replaceState(state, "", url);
  else { replaceListRoute(); history.pushState(state, "", url); }
}
async function restoreNavigation({markViewed = false} = {}) {
  const route = history.state?.pilot;
  window.PilotAssistant?.restore(route);
  if (route?.view === "thread" && sessions.some(session => session.id === route.session)) {
    await selectSession(route.session, {fromHistory: true, markViewed});
  } else {
    if (route?.view === "list") listScrollTop = route.listScrollTop || 0;
    else replaceListRoute();
    showSessionList();
  }
}
window.addEventListener("popstate", () => {
  returningToList = false; $("back").disabled = false;
  restoreRoute = true;
  if (history.state?.pilot?.view !== "thread" || sessions.length) {
    restoreRoute = false; restoreNavigation({markViewed: true});
  } else if (token) poll();
});

function threadScroller() { return mobileLayout.matches ? document.scrollingElement : $("scroll"); }
function readLatestMessage() {
  const inset = mobileLayout.matches ? $("thread").querySelector(".thread-top").getBoundingClientRect().height + 12 : 12;
  if (current()?.runtime?.approvals?.length && $("approval-panel").children.length) {
    window.PilotReading.startAt($("approval-panel").children[0], threadScroller(), inset);
    return;
  }
  window.PilotReading.start($("messages"), threadScroller(), inset);
}
function threadVisible() { return (!mobileLayout.matches && window.PilotAssistant?.mode() !== "assistant") || $("workspace").classList.contains("detail"); }
function positionLatest() {
  const entry = pendingLatest;
  if (!entry || entry.scheduled || document.hidden || !threadVisible()) return;
  entry.scheduled = true;
  const readingTop = threadScroller().scrollTop;
  // A notification can wake an existing iPhone page with unchanged messages.
  // Wait until the revealed document has laid out; do not depend on a message diff.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (pendingLatest !== entry) return;
    entry.scheduled = false;
    if (selected !== entry.id || viewGeneration !== entry.view || token !== entry.token || !threadVisible()) {
      pendingLatest = null; return;
    }
    if (document.hidden) return; // The foreground poll will retry after a successful load.
    const scroll = threadScroller();
    if (scroll.scrollTop !== readingTop) { pendingLatest = null; return; }
    if (!window.PilotAssistant?.positionLatest()) readLatestMessage();
    threadScrollTop = scroll.scrollTop;
    pendingLatest = null;
  }));
}
function showSessionList() {
  window.PilotVoice?.leave();
  pendingLatest = null;
  viewGeneration++;
  if (selected) { drafts.set(selected, $("message").value); if (threadVisible()) threadPositions.set(selected, threadScroller().scrollTop); }
  if (mobileLayout.matches && threadVisible()) threadScrollTop = document.scrollingElement.scrollTop;
  $("workspace").classList.remove("detail");
  window.PilotAssistant?.refresh();
  if (mobileLayout.matches) window.scrollTo(0, listScrollTop);
}

async function api(path, data, {timeoutMs = 23000} = {}) {
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      method: data === undefined ? "GET" : "POST", signal: controller.signal,
      headers: {Authorization: `Bearer ${token}`, ...(data === undefined ? {} : {"Content-Type": "application/json"})},
      ...(data === undefined ? {} : {body: JSON.stringify(data)}), cache: "no-store"
    });
    const result = await response.json();
    if (response.status === 401) { logout(); throw new Error("連線已失效，請重新配對。"); }
    if (!response.ok) { const error = new Error(result.error || "操作未完成。"); error.status = response.status; throw error; }
    return result;
  } finally { clearTimeout(timeout); }
}
function connection(ok) {
  connected = ok;
  $("connection-label").textContent = ok ? (document.body?.dataset?.service === "brain" ? "已連接 Brain 背景服務" : "已連接這台 Mac") : "連線中斷，正在重連…";
  $("connection-light").classList.toggle("offline", !ok);
  updateComposer();
  renderAttention(current());
  renderProgress(current());
  window.PilotApprovals?.update(current());
  window.PilotWindows?.update(current());
  window.PilotAssistant?.refresh();
}
let styleHealthReported = "";
async function reportStyleHealth() {
  if (!token || typeof getComputedStyle !== "function") return;
  const link = document.querySelector('link[rel="stylesheet"]');
  const applied = getComputedStyle(document.documentElement).getPropertyValue("--paper").trim() === "#f7f6f2";
  const signature = `${Boolean(link?.sheet)}:${applied}`;
  if (styleHealthReported === signature) return;
  styleHealthReported = signature;
  const report = {applied, sheetLoaded: Boolean(link?.sheet), standalone: navigator.standalone === true || window.matchMedia("(display-mode: standalone)").matches};
  report.sheetDisabled = Boolean(link?.disabled || link?.sheet?.disabled);
  try {
    const rules = Array.from(link?.sheet?.cssRules || []);
    report.ruleCount = rules.length;
    report.rootRulePresent = rules.some(rule => rule.selectorText === ":root");
    report.rulesReadable = true;
  } catch { report.rulesReadable = false; }
  const url = new URL(link?.href || "/style.css", location.origin).href;
  const timing = performance.getEntriesByName(url).at(-1);
  for (const key of ["duration", "transferSize", "encodedBodySize", "decodedBodySize", "responseStatus"]) {
    if (Number.isFinite(timing?.[key])) report[key] = Math.max(0, Math.min(10_000_000, timing[key]));
  }
  if (!applied) {
    // Diagnose the failed load; do not replace styles, reload the page, retry
    // user actions or change how the stylesheet is applied.
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(url, {cache: "no-store", signal: controller.signal});
      report.probeStatus = response.status;
      report.probeType = (response.headers.get("content-type") || "").slice(0, 80);
      const bytes = await response.arrayBuffer(); report.probeBytes = bytes.byteLength;
      if (crypto.subtle) report.probeHash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
    } catch (error) { report.probeError = ["AbortError", "TypeError", "NetworkError"].includes(error.name) ? error.name : "Error"; }
    finally { clearTimeout(timeout); }
  }
  try { await api("/api/diagnostics/styles", report); }
  catch { styleHealthReported = ""; }
}
function showWorkspace() { $("pairing").hidden = true; $("workspace").hidden = false; reportStyleHealth(); poll(); }
function logout() {
  token = ""; sessionStorage.removeItem("pilot-token"); connected = false;
  try { localStorage.removeItem("pilot-token"); } catch {}
  window.PilotPush?.update([]);
  window.PilotLaunch?.reset();
  window.PilotVoice?.leave();
  window.PilotAssistant?.reset();
  sessions = []; selected = ""; threadPages.clear(); threadPositions.clear(); drafts.clear(); feedback.clear(); userMessages.clear(); messageKey = ""; sessionListKey = ""; selectionGeneration++;
  acknowledgedNotices.clear(); notificationSessions = null; viewGeneration++; pendingLatest = null;
  progressMessages.clear();
  terminalAttempts.clear();
  $("messages").replaceChildren(); $("terminal-text").textContent = ""; $("message").value = "";
  $("workspace").hidden = true; $("pairing").hidden = false; $("pair-code").value = "";
  listScrollTop = 0; threadScrollTop = 0; window.scrollTo(0, 0);
  replaceListRoute(); restoreRoute = true;
}
$("disconnect").addEventListener("click", async () => {
  try { await window.PilotPush?.unsubscribe(); logout(); }
  catch (error) { $("connection-label").textContent = `未能登出：${error.message}`; }
});
$("pair-form").addEventListener("submit", async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; $("pair-error").textContent = "";
  try { const result = await api("/api/pair", {code: $("pair-code").value.trim()}); token = result.token; rememberToken(); showWorkspace(); }
  catch (error) { $("pair-error").textContent = error.message || "無法連線。"; }
  finally { button.disabled = false; }
});
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function relativeTime(timestamp) {
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - timestamp) / 60));
  return minutes < 1 ? "剛剛" : minutes < 60 ? `${minutes} 分鐘前` : minutes < 1440 ? `${Math.floor(minutes / 60)} 小時前` : `${Math.floor(minutes / 1440)} 天前`;
}
function renderSessions() {
  window.PilotPush?.update(sessions);
  window.PilotAssistant?.refresh();
  const key = JSON.stringify([sessions, selected, needsOnly, $("search").value, Math.floor(Date.now() / 60000)]);
  if (sessionListKey === key) return;
  sessionListKey = key;
  $("work-count").textContent = sessions.length;
  $("needs-count").textContent = sessions.filter(s => s.unread).length;
  const query = $("search").value.trim().toLocaleLowerCase();
  const visible = sessions.filter(s => (!needsOnly || s.unread) && `${s.title} ${s.directory} ${agentName(s)}`.toLocaleLowerCase().includes(query));
  const existing = new Map(Array.from($("sessions").children, node => [node.dataset.session, node]));
  const items = visible.map(session => {
    let button = existing.get(session.id);
    if (!button) {
      button = element("button"); button.dataset.session = session.id;
      button.addEventListener("click", () => selectSession(session.id));
    }
    button.className = `session${session.id === selected ? " selected" : ""}${session.unread ? " needed" : ""}`;
    button.setAttribute("aria-current", session.id === selected ? "true" : "false");
    const status = session.phase === "waiting" ? attentionState(session)?.title || session.status : session.status;
    const signature = JSON.stringify([session.title, session.directory, agentName(session), session.unread, status, relativeTime(session.updatedAt)]);
    if (button.dataset.signature === signature) return button;
    button.dataset.signature = signature;
    const top = element("div", "session-top"); top.append(element("span", "session-name", session.title));
    if (session.unread) { const dot = element("span", "dot"); dot.setAttribute("aria-label", "需要你處理"); top.append(dot); }
    const bottom = element("div", "session-bottom"); bottom.append(element("span", "", status), element("span", "", relativeTime(session.updatedAt)));
    button.replaceChildren(top, element("p", "session-path", `${agentName(session)} · ${session.directory || "未連接 agent"}`), bottom);
    return button;
  });
  if (!items.length) items.push(element("p", "list-empty", sessions.length ? "目前沒有符合的工作。" : "還沒有工作視窗。\n請從 Terminal Pilot 開啟工作，或續接 Codex／Claude Code 對話。"));
  const keep = new Set(items);
  for (const node of Array.from($("sessions").children)) if (!keep.has(node)) node.remove();
  items.forEach((node, index) => { if ($("sessions").children[index] !== node) $("sessions").insertBefore(node, $("sessions").children[index] || null); });
}
function agentName(session) { return session?.toolName || (session?.tool === "claude" ? "Claude Code" : session?.tool === "terminal" ? "Terminal" : "Codex CLI"); }
function current() { return sessions.find(s => s.id === selected); }
function withAcknowledgment(session) {
  if (!session.noticeID || acknowledgedNotices.get(session.id) !== session.noticeID) return session;
  return {...session, unread: false, status: session.phase === "complete" ? "最近回報：回覆完成" : session.status};
}
async function acknowledgeNotice(id, noticeID) {
  const activeToken = token;
  try {
    const result = await api("/api/ack", {session: id, noticeID});
    if (token !== activeToken || !token) return;
    if (!result.acknowledged) throw new Error("尚未確認已讀，請稍後再試。");
    acknowledgedNotices.set(id, noticeID);
    sessions = sessions.map(withAcknowledgment);
    notificationSessions = (notificationSessions || sessions).map(withAcknowledgment);
    window.PilotPush?.update(notificationSessions);
    renderSessions(); renderHeader();
    await poll();
  } catch (error) {
    if (token !== activeToken || !token) return;
    feedback.set(id, {text: `未能標示已讀：${error.message}`, error: true});
    if (selected === id) renderHeader();
  }
}
function updateComposer() {
  const session = current(), pending = sending.has(selected);
  const terminal = session?.tool === "terminal";
  $("send").disabled = !connected || !session?.canSend || pending || !$("message").value.trim();
  $("message").disabled = !session || pending;
  $("message").placeholder = terminal ? "輸入 Terminal 指令或文字…" : "傳送下一個想法…";
  $("compose-label").textContent = terminal ? "Terminal 指令或文字" : "回覆這個工作";
  $("terminal-enter").hidden = !terminal;
  $("terminal-enter").disabled = !connected || !session?.canSend || pending;
  $("send").firstChild.textContent = pending ? "傳送中…" : "送出 ";
  $("send-hint").textContent = !connected ? "與 Mac 連線中斷，草稿已保留，正在重連…" : pending ? "上一則回覆正在傳送，請稍候" : session?.phase === "waiting" ? "正在等待核准或選項，請先處理上方確認" :
    !session?.binding || session?.alive === false || session?.phase === "ended" ? "此工作已結束，請從專案紀錄重新開啟" :
    !session?.canSend ? `${agentName(session)} 尚未準備好接收輸入，請查看工作狀態` : "Enter 換行 · ⌘ / Ctrl + Enter 送出";
  $("reconnect").hidden = connected;
}
function attentionState(session) {
  if (!session || !["codex", "claude"].includes(session.tool)) return null;
  if (session.phase === "waiting" || session.runtime?.approvals?.length) {
    const question = session.runtime?.approvals?.some(r => r.kind === "question") || session.noticeReason === "user_input" || ["AskUserQuestion", "request_user_input", "functions.request_user_input", "request_user_input_async", "functions.request_user_input_async"].includes(session.waitingTool);
    const human = session.approvalReviewer === "user" || session.runtime?.approvals?.length;
    const automatic = ["auto_review", "guardian_subagent"].includes(session.approvalReviewer) && !question;
    return {
      kind: "waiting",
      title: question ? "正在等你回答選項" : automatic ? "工具權限正在審查" : human ? "正在等你按 Allow／允許" : "工具權限等待確認",
      detail: `${session.waitingTool ? `工具：${session.waitingTool}。` : ""}${automatic ? "目前尚未確認需要你核准。" : "這個步驟尚未完成，Agent 正在等待。"}${session.runtime?.approvals?.length ? "確認內容在最新對話下方，可直接選擇允許或拒絕。" : session.runtime?.canApprove ? "正在等候 Codex 傳送確認內容；未收到前也可查看電腦原提示。" : "這個工作尚未接上手機核准通道，請使用下方續接功能，或在電腦處理原提示。"}`,
      action: "查看 Terminal 提示"
    };
  }
  if (session.tool === "codex" && session.codexSocket && session.codexThreadID && session.runtime?.state === "unavailable" && session.alive !== false) {
    return {kind: "setup", title: "手機核准連線尚未恢復",
      detail: "對話紀錄仍可查看，正在重新連接核准通道。接通前，新的 Allow 要求請先在電腦確認。",
      action: "查看 Terminal 內容"};
  }
  if (session.tool === "codex" && session.lifecycleReady === false && !["running", "idle", "approval", "question"].includes(session.runtime?.state) && !["ended", "error"].includes(session.phase)) {
    if (session.codexSocket) return {kind: "setup", title: "等待 Codex 對話連接",
      detail: "核准通道已啟動，尚未連上對話。首次啟動、更新提示請先在電腦完成；新對話送出第一句後會自動對應。",
      action: "查看 Terminal 內容"};
    return {kind: "setup", title: "這個工作的等待通知尚未接通",
      detail: "可以查看對話與本輪計時；這個舊工作尚未接上手機核准。使用「接通手機核准」保留對話並續接，之後即可在手機處理工具要求。",
      action: "查看 Terminal 內容"};
  }
  return null;
}
function renderAttention(session) {
  const state = attentionState(session);
  $("attention-banner").hidden = !state;
  if ($("attention-banner").className !== `attention-banner ${state?.kind || ""}`) $("attention-more").open = state?.kind === "waiting" && window.PilotAssistant?.mode() !== "assistant";
  $("attention-banner").className = `attention-banner ${state?.kind || ""}`;
  if ($("attention-title").textContent !== (state?.title || "")) $("attention-title").textContent = state?.title || "";
  if ($("attention-detail").textContent !== (state?.detail || "")) $("attention-detail").textContent = state?.detail || "";
  $("attention-read").textContent = state?.action || "查看 Terminal 提示";
  $("attention-read").disabled = !connected || !session?.binding;
}
function progressState(session) {
  if (!session || !["codex", "claude"].includes(session.tool)) return null;
  const message = progressMessages.get(session.id), runtime = session.runtime?.state;
  const recent = message && new Date(message.time).valueOf() / 1000 > (session.updatedAt || 0);
  let title = session.status || "等待更新", working = false;
  if (!connected) title = "連線中斷，進度尚未更新";
  else if (session.phase === "waiting") title = attentionState(session)?.title || "等待你處理";
  else if (runtime === "running" || (session.phase === "running" && session.lifecycleReady !== false)) { title = "處理中"; working = true; }
  else if (runtime === "idle") title = session.phase === "complete" || message?.phase === "final_answer" ? "回覆完成" : "等待你的指示";
  else if (runtime === "error" || session.phase === "error") title = "工作發生錯誤";
  else if (session.alive && session.turnProgress?.state === "active") { title = "本輪進行中"; working = true; }
  else if (session.phase !== "ended" && recent && (message.role === "user" || message.phase === "commentary")) {
    title = "本輪尚未收到完成回報";
  } else if (message?.phase === "final_answer" && recent) title = "回覆完成";
  else if (session.phase === "running") title = "即時狀態未連接，等待新的回報";
  const preview = message?.role === "assistant" ? message.text.replace(/\s+/g, " ").slice(0, 240) : "";
  const stamp = message?.time && new Date(message.time).valueOf() / 1000;
  const pending = session.phase !== "ended" && recent && (message.role === "user" || message.phase === "commentary");
  const fromMessage = progressStarts.get(session.id) || 0;
  const formalStart = session.turnProgress?.state === "active" ? session.turnProgress.startedAt : session.activeTurnStartedAt || 0;
  const start = formalStart || fromMessage;
  let elapsed = "";
  if (session.alive !== false && (working || session.phase === "waiting" || pending) && start > 0) {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - start));
    const duration = seconds >= 3600 ? `${Math.floor(seconds / 3600)} 小時 ${Math.floor(seconds % 3600 / 60)} 分` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
    elapsed = `${formalStart ? "本輪已經過" : "距離送出"} ${duration}${!formalStart && runtime === "unavailable" && !session.lifecycleReady ? "（尚未連上即時狀態）" : ""}`;
  }
  return {title, working, preview, elapsed, time: stamp ? `最新回報 · ${relativeTime(stamp)}` : "等待進度回報"};
}
function renderProgress(session) {
  const state = progressState(session);
  $("progress-latest").textContent = session?.runtime?.approvals?.length ? "待確認 ↓" : "最新 ↓";
  $("progress-panel").hidden = !state;
  if (!state) return;
  $("progress-panel").className = `progress-panel${state.working ? " working" : ""}`;
  for (const [id, text] of [["progress-title", state.title], ["progress-time", state.time]]) {
    if ($(id).textContent !== text) $(id).textContent = text;
  }
  $("progress-time").hidden = Boolean(state.elapsed);
  $("progress-elapsed").textContent = state.elapsed;
  $("progress-elapsed").hidden = !state.elapsed;
}
function renderHeader() {
  const session = current();
  window.PilotAssistant?.selection(session);
  renderAttention(session);
  renderProgress(session);
  window.PilotApprovals?.update(session);
  window.PilotWindows?.update(session);
  window.PilotVoice?.update(session);
  if (!session) {
    if ($("workspace").classList.contains("detail")) { replaceListRoute(); showSessionList(); }
    $("thread").hidden = true; $("empty").hidden = false; return;
  }
  $("thread").hidden = false; $("empty").hidden = true;
  $("thread-title").textContent = session.title; $("thread-directory").textContent = session.directory;
  const goal=$("thread-goal"), context=window.PilotAssistant?.workContext?.(session.id) || "";
  if (goal) { goal.textContent=context; goal.hidden=!context; }
  const approvalConnection = session.tool === "codex" && session.codexSocket && session.alive !== false
    ? session.runtime?.canApprove ? " · 手機核准已連接" : " · 手機核准連接中" : "";
  $("thread-status").textContent = `${agentName(session)}${approvalConnection}`;
  $("thread-status").classList.toggle("needed", session.unread);
  $("ack").hidden = !session.unread;
  $("activity").hidden = true;
  $("compose").hidden = Boolean(session.readOnly) || !["terminal", "codex", "claude", undefined].includes(session.tool);
  $("compose-note").textContent = session.tool === "terminal" ? "送出會把文字直接輸入這個 Terminal 並按 Enter；多行內容會依序送入。只按 Enter 可回應目前的提示。" : session.lifecycleReady ? "點進對話會清除當下提醒；新的通知仍會亮紅點，送出回覆／恢復工作也會清除。" : session.codexSocket ? "等待 Codex 連上對話；接通後，送出回覆／恢復工作會清除提醒。" : "點進對話會清除當下提醒；舊工作可使用「接通手機核准」保留對話並續接。";
  if (session.readOnly) $("compose-note").textContent = `這是 Brain 的原始 Codex 對話（最近 20 回合，最多 150 則）。回到「個人助理」繼續交代。\nCodex thread：${session.codexThreadID}`;
  const note = feedback.get(selected);
  $("feedback").textContent = note?.text || ""; $("feedback").classList.toggle("failure", Boolean(note?.error));
  updateComposer();
}
async function selectSession(id, {fromHistory = false, markViewed = true, latest = false} = {}) {
  const session = sessions.find(session => session.id === id);
  if (!session) return;
  window.PilotLaunch?.cancelNavigation();
  window.PilotVoice?.leave();
  // Capture the exact notification chosen by the user, never one arriving while loading.
  const noticeID = markViewed && session.unread ? session.noticeID : "";
  const view = ++viewGeneration, activeToken = token;
  pendingLatest = latest ? {id, view, token: activeToken, scheduled: false} : null;
  const changed = selected !== id;
  if (changed || latest) selectionGeneration++;
  if (mobileLayout.matches && !threadVisible()) listScrollTop = document.scrollingElement.scrollTop;
  if (!fromHistory) recordThreadRoute(id);
  if (selected) { drafts.set(selected, $("message").value); if (threadVisible()) threadPositions.set(selected, threadScroller().scrollTop); }
  if (changed) {
    selected = id; messageKey = "";
    $("messages").replaceChildren(); $("history-note").hidden = true;
    $("terminal-panel").hidden = true; $("terminal-text").textContent = "";
  }
  $("message").value = drafts.get(id) || "";
  $("workspace").classList.add("detail"); renderSessions(); renderHeader();
  if (mobileLayout.matches) window.scrollTo(0, changed ? 0 : threadScrollTop);
  const loaded = await loadThread();
  if (!latest && threadPositions.has(id)) { const saved = threadPositions.get(id); requestAnimationFrame(() => { if (selected === id && view === viewGeneration) threadScroller().scrollTop = saved; }); }
  if (loaded && noticeID && view === viewGeneration && selected === id &&
      threadVisible() && !document.hidden && token && token === activeToken) {
    await acknowledgeNotice(id, noticeID);
  }
}
$("back").addEventListener("click", () => {
  if (returningToList) return;
  if (history.state?.pilot?.view === "thread" && history.length > 1) {
    returningToList = true; $("back").disabled = true; history.back();
  } else { replaceListRoute(); showSessionList(); }
});
$("search").addEventListener("input", renderSessions);
for (const [id, needed] of [["filter-all", false], ["filter-needed", true]]) $(id).addEventListener("click", () => {
  needsOnly = needed; $("filter-all").classList.toggle("active", !needed); $("filter-needed").classList.toggle("active", needed); renderSessions();
});
// Deliberately small renderer: model text never becomes HTML, scripts or external resources.
function inline(node, text) {
  for (const part of text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g)) {
    if (part.startsWith("`") && part.endsWith("`")) node.append(element("code", "", part.slice(1, -1)));
    else if (part.startsWith("**") && part.endsWith("**")) node.append(element("strong", "", part.slice(2, -2)));
    else node.append(document.createTextNode(part));
  }
}
function tableRow(line) {
  const cells = []; let cell = "", separators = 0;
  const text = line.trim();
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "\\" && i + 1 < text.length) {
      const next = text[++i]; cell += next === "|" ? next : "\\" + next;
    } else if (text[i] === "`") {
      const marker = text.slice(i).match(/^`+/)[0], end = text.indexOf(marker, i + marker.length);
      if (end !== -1) { cell += text.slice(i, end + marker.length); i = end + marker.length - 1; }
      else { cell += marker; i += marker.length - 1; }
    } else if (text[i] === "|") { cells.push(cell.trim()); cell = ""; separators++; }
    else cell += text[i];
  }
  cells.push(cell.trim());
  if (!separators) return null;
  if (text.startsWith("|")) cells.shift();
  // An escaped final pipe is content, so use the parsed final cell, not a suffix regex.
  if (cells.at(-1) === "" && text.endsWith("|")) cells.pop();
  return cells;
}
function tableHeader(line, divider) {
  const cells = tableRow(line), rules = tableRow(divider || "");
  if (!cells?.length || cells.length !== rules?.length || !rules.every(s => /^:?-{3,}:?$/.test(s))) return null;
  return {cells, align: rules.map(s => s.endsWith(":") ? s.startsWith(":") ? "center" : "right" : "left")};
}
function renderTable(header, rows) {
  const region = element("div", "message-table-scroll");
  region.tabIndex = 0; region.setAttribute("role", "region"); region.setAttribute("aria-label", "表格，可左右捲動查看完整內容");
  const table = element("table", "message-table"), head = element("thead"), body = element("tbody");
  function row(values, heading) {
    const tr = element("tr");
    for (let i = 0; i < header.cells.length; i++) {
      const cell = element(heading ? "th" : "td", `table-${header.align[i]}`);
      if (heading) cell.setAttribute("scope", "col");
      inline(cell, values[i] || ""); tr.append(cell);
    }
    return tr;
  }
  head.append(row(header.cells, true));
  for (const values of rows) body.append(row(values, false));
  table.append(head, body); region.append(table); return region;
}
function renderText(text) {
  const body = element("div", "message-body"), lines = text.split("\n");
  let paragraph = [], list = null;
  function flush() { if (paragraph.length) { const p = element("p"); inline(p, paragraph.join("\n")); body.append(p); paragraph = []; } list = null; }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush(); const language = line.trim().slice(3), code = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      const block = element("div", "code-block"), label = element("div", "code-label", language || "code"), copy = element("button", "text-button", "複製");
      copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(code.join("\n")); copy.textContent = "已複製"; } catch { copy.textContent = "請選取文字複製"; } });
      label.append(copy); const pre = element("pre"); pre.append(element("code", "", code.join("\n"))); block.append(label, pre); body.append(block);
    } else if (tableHeader(line, lines[i + 1])) {
      flush(); const header = tableHeader(line, lines[++i]), rows = [];
      while (i + 1 < lines.length) {
        const next = lines[i + 1];
        if (/^\s*(?:```|#{1,6}\s|[-*]\s|\d+\.\s)/.test(next)) break;
        const cells = tableRow(next);
        // Do not discard unexpected extra cells or consume a following table.
        if (!cells || cells.length > header.cells.length || tableHeader(next, lines[i + 2])) break;
        rows.push(cells); i++;
      }
      body.append(renderTable(header, rows));
    } else if (/^#{1,6}\s/.test(line)) {
      flush(); const heading = element(line.startsWith("###") ? "h4" : "h3"); inline(heading, line.replace(/^#+\s/, "")); body.append(heading);
    } else if (/^\s*(?:[-*]|\d+\.)\s/.test(line)) {
      if (paragraph.length) flush(); const tag = /^\s*\d/.test(line) ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== tag) { list = element(tag); body.append(list); }
      const li = element("li"); inline(li, line.replace(/^\s*(?:[-*]|\d+\.)\s/, "")); list.append(li);
    } else if (!line.trim()) flush();
    else { list = null; paragraph.push(line); }
  }
  flush(); return body;
}
async function loadThread() {
  if (!selected || !token) return;
  const id = selected, generation = selectionGeneration;
  try {
    let cached = threadPages.get(id);
    let result = await api(`/api/thread?session=${encodeURIComponent(id)}${cached?.cursor != null ? `&after=${cached.cursor}` : ""}`);
    if (selected !== id || generation !== selectionGeneration) return;
    if (cached && (cached.thread !== result.thread || cached.revision !== result.revision || cached.session.binding !== result.session.binding)) {
      cached=null; threadPages.delete(id); threadPositions.delete(id);
      result=await api(`/api/thread?session=${encodeURIComponent(id)}`);
      if (selected !== id || generation !== selectionGeneration) return;
      messageKey="";
    }
    if (result.cursor != null) {
      const known = new Map((cached?.messages || []).map(m => [m.id,m]));
      for (const m of result.messages) known.set(m.id,m);
      result.messages = [...known.values()].sort((a,b) => a.cursor-b.cursor);
      result.hasMore = cached ? cached.hasMore : result.hasMore;
      threadPages.set(id, {...result});
    }
    const earlier = $("thread-earlier");
    if (earlier) { earlier.hidden = !result.hasMore; earlier.disabled = loadingThreadEarlier; }
    sessions = sessions.map(s => s.id === id ? withAcknowledgment(result.session) : s);
    const delivered = feedback.get(id);
    if (delivered?.kind === "history") feedback.delete(id);
    if (delivered?.sentText && result.messages.some(m => m.role === "user" && m.text.trim() === delivered.sentText.trim() && !delivered.knownUsers.includes(m.id))) feedback.delete(id);
    userMessages.set(id, result.messages.filter(m => m.role === "user").map(m => m.id));
    // Hidden conversations must not move the work list. Follow only when already
    // near the end, so updates do not pull someone away from older messages.
    const scroll = threadScroller(), follow = threadVisible() &&
      (messageKey === "" || scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 130);
    const lastVisible = result.messages.at(-1);
    const lastPrompt = result.messages.findLast(m => m.role === "user");
    if (lastPrompt) progressStarts.set(id, new Date(lastPrompt.time).valueOf() / 1000);
    if (lastVisible) progressMessages.set(id, lastVisible); else progressMessages.delete(id);
    renderHeader();
    const key = JSON.stringify(result.messages);
    window.PilotVoice?.thread(result);
    window.PilotAssistant?.thread(result);
    if (result.source === "terminal") {
      $("history-note").hidden = true;
      const text = result.terminalText || "目前沒有文字內容。";
      const terminalKey = "terminal:" + text;
      if (messageKey !== terminalKey) {
        const readingTop = scroll.scrollTop;
        messageKey = terminalKey;
        $("messages").replaceChildren(element("pre", "terminal-output", text));
        scroll.scrollTop = readingTop;
        if (follow) requestAnimationFrame(() => {
          if (selected === id && generation === selectionGeneration && threadVisible()) scroll.scrollTop = scroll.scrollHeight;
        });
      }
      pendingLatest = null;
      return true;
    }
    $("history-note").hidden = !result.historyError && !["none", "terminal"].includes(result.source);
    $("history-note").textContent = result.historyError || (result.source === "terminal" ? "這是一般 Terminal 工作，可按「查看 Terminal 內容」閱讀快照。" : `尚未對應到這個工作的對話紀錄。可先查看 Terminal 內容；${agentName(result.session)} 下次回報後會再嘗試對應。`);
    if (key !== messageKey) {
      const firstLoad = messageKey === "";
      const previousLatest = window.PilotReading.latest($("messages"))?.dataset.messageId;
      const readingTop = scroll.scrollTop;
      messageKey = key;
      const messages = result.messages.map(message => {
        const article = element("article", `message ${message.role}`), label = element("div", "message-label", message.role === "user" ? "你" : `${agentName(result.session)}${message.phase === "commentary" ? " · 處理進度" : ""}`);
        const date = new Date(message.time);
        if (!Number.isNaN(date.valueOf())) { const time = element("time", "", date.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})); time.dateTime = message.time; label.append(time); }
        article.dataset.messageId = String(message.id);
        article.dataset.readingTarget = String(message.role !== "system");
        article.append(label, renderText(message.text)); return article;
      });
      $("messages").replaceChildren(...messages);
      scroll.scrollTop = readingTop;
      if (follow && (firstLoad || previousLatest !== window.PilotReading.latest($("messages"))?.dataset.messageId)) {
        if (window.PilotAssistant?.briefVisible()) { if (firstLoad) window.PilotAssistant.positionLatest(); }
        else {
          pendingLatest ||= {id, view: viewGeneration, token, scheduled: false};
        }
      }
    }
    positionLatest();
    return true;
  } catch (error) {
    if (selected === id && token) {
      if (!feedback.get(id) || feedback.get(id)?.kind === "history") feedback.set(id, {
        text: error instanceof TypeError || error.name === "AbortError" ? "暫時連不上 Mac，正在重新取得內容；草稿已保留。" : `內容尚未更新：${error.message}`,
        error: true, kind: "history"});
      renderHeader();
    }
  }
}
$("thread-earlier")?.addEventListener("click", async () => {
  const id = selected, cache = threadPages.get(id), generation = selectionGeneration;
  if (!cache?.hasMore || loadingThreadEarlier) return;
  loadingThreadEarlier = true;
  const scroll = threadScroller(), height = scroll.scrollHeight, top = scroll.scrollTop;
  try {
    const result = await api(`/api/thread?session=${encodeURIComponent(id)}&before=${cache.messages[0].cursor}`);
    if (id !== selected || generation !== selectionGeneration) return;
    if (result.thread !== cache.thread || result.revision !== cache.revision || result.session.binding !== cache.session.binding) { threadPages.delete(id); await loadThread(); return; }
    const known = new Map([...result.messages,...cache.messages].map(m => [m.id,m]));
    threadPages.set(id, {...cache, messages:[...known.values()].sort((a,b) => a.cursor-b.cursor), hasMore:result.hasMore});
    await loadThread();
    requestAnimationFrame(() => { if (selected === id && generation === selectionGeneration) scroll.scrollTop = top + scroll.scrollHeight-height; });
  } catch (error) { feedback.set(id,{text:error.message,error:true,kind:"history"});renderHeader(); }
  finally { loadingThreadEarlier = false; if ($("thread-earlier")) $("thread-earlier").disabled=false; }
});
async function poll() {
  if (!token || polling) return; polling = true;
  try {
    const activeToken = token, result = await api("/api/state");
    if (!token || token !== activeToken) return;
    const previous = new Map(sessions.map(s => [s.id, s]));
    sessions = result.sessions.map(s => {
      const old = previous.get(s.id);
      // The list omits transcript metadata. Keep the exact thread's clock while
      // its next history request is in flight, instead of flashing a reset timer.
      if (old?.binding === s.binding && old?.codexThreadID === s.codexThreadID && old?.activeTurn === s.activeTurn && old?.turnProgress) {
        s = {...s, turnProgress: old.turnProgress};
      }
      return withAcknowledgment(s);
    });
    connection(true); renderSessions(); renderHeader();
    notificationSessions = (result.notifications || sessions).map(withAcknowledgment);
    window.PilotPush?.update(notificationSessions, result.httpsURL);
    window.PilotLaunch?.update(result);
    window.PilotAssistant?.update(result);
    const openedWork = window.PilotApprovals?.takeOpenedWork(sessions) || window.PilotLaunch?.takeOpenedWork(sessions);
    if (incomingWork) {
      const id = incomingWork; incomingWork = ""; restoreRoute = false;
      if (id === "brain" || id.startsWith('brain:')) {
        window.PilotAssistant?.chooseMode("assistant");
        window.PilotAssistant?.switchTopic?.(id.startsWith('brain:') ? id.slice(6) : '', {latest: true});
      }
      else if (sessions.some(session => session.id === id)) {
        window.PilotAssistant?.chooseMode?.("manager");
        await selectSession(id, {latest: true});
      }
      else { replaceListRoute(); showSessionList(); }
    } else if (openedWork) {
      window.PilotAssistant?.chooseMode?.("manager");
      restoreRoute = false; await selectSession(openedWork, {latest: true});
    } else if (restoreRoute) {
      restoreRoute = false; await restoreNavigation();
    } else if (current() && threadVisible()) await loadThread();
    if (!selected && sessions.length && !mobileLayout.matches && window.PilotAssistant?.mode() !== "assistant") await selectSession(sessions[0].id, {markViewed: false});
  } catch { if (token) connection(false); }
  finally { polling = false; }
}
function openPushWork(id) {
  if (typeof id !== "string" || !id) { replaceListRoute(); showSessionList(); return; }
  incomingWork = id;
  if (token) poll();
}
$("message").addEventListener("input", () => { drafts.set(selected, $("message").value); updateComposer(); });
$("message").addEventListener("change", () => { drafts.set(selected, $("message").value); updateComposer(); });
$("message").addEventListener("compositionend", updateComposer);
$("reconnect").addEventListener("click", () => poll());
$("message").addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && !event.isComposing) { event.preventDefault(); if (!$("send").disabled) $("compose").requestSubmit(); }
});
function requestID() {
  const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
  return Array.from(bytes, x => x.toString(16).padStart(2, "0")).join("");
}
setInterval(() => { if (selected && !document.hidden) renderProgress(current()); }, 1000);
const terminalAttempts = new Map();
async function sendAgentReply(target, text, identity, {clearDraft = false, terminal = false} = {}) {
  const session = current(), activeToken = token;
  if (!connected || !activeToken) throw new Error("連線中斷，尚未送出。");
  if (!session?.canSend || session.id !== target.session || session.binding !== target.binding)
    throw new Error("工作狀態已改變，尚未送出。");
  if (terminal !== (session.tool === "terminal")) throw new Error("輸入模式與目前工作不同，尚未送出。");
  if (sending.has(session.id)) throw new Error("這個工作正在傳送回覆，請稍候。");
  const id = session.id, knownUsers = userMessages.get(id) || []; sending.add(id); feedback.delete(id); renderHeader();
  if (terminal) {
    const prior = terminalAttempts.get(id);
    if (prior?.binding === target.binding && prior.text === text) identity = prior.identity;
    else terminalAttempts.set(id, {binding: target.binding, text, identity});
  }
  try {
    const result = await api(terminal ? "/api/terminal/input" : "/api/send", {session: id, binding: target.binding, text, requestID: identity});
    if (!result.sent) throw new Error("無法確認是否已送出，請先查看 Terminal 內容，避免重複送出。");
    if (terminal) terminalAttempts.delete(id);
    if (token === activeToken) {
      if (clearDraft) {
        if (drafts.get(id) === text) drafts.set(id, "");
        if (selected === id && $("message").value === text) $("message").value = "";
      }
      feedback.set(id, {text: terminal ? "已輸入 Terminal，執行結果會顯示在上方。" : `已送到 Terminal，等待 ${agentName(session)} 接收。若尚未開始，請查看 Terminal 內容。`, sentText: text, knownUsers});
    }
    return result;
  } catch (error) {
    if (token === activeToken) feedback.set(id, {text: `${error.name === "AbortError" || error instanceof TypeError ? "連線中斷，無法確認是否已送出。" : error.message}\n${clearDraft ? "草稿已保留；" : ""}請先查看 Terminal 內容，再決定是否重送。`, error: true});
    throw error;
  } finally { sending.delete(id); renderHeader(); poll(); }
}
$("compose").addEventListener("submit", async event => {
  event.preventDefault(); const session = current(), text = $("message").value;
  if (!session || $("send").disabled) return;
  try { await sendAgentReply({session: session.id, binding: session.binding}, text, requestID(), {clearDraft: true, terminal: session.tool === "terminal"}); }
  catch { /* The shared sender keeps the draft and displays delivery feedback. */ }
});
$("terminal-enter").addEventListener("click", async () => {
  const session = current();
  if (session?.tool !== "terminal" || $("terminal-enter").disabled) return;
  try { await sendAgentReply({session: session.id, binding: session.binding}, "", requestID(), {terminal: true}); }
  catch { /* Same delivery protection and feedback as typed input. */ }
});
async function readTerminal() {
  const session = current(); if (!session) return;
  const id = session.id, generation = selectionGeneration;
  $("terminal-panel").hidden = false; $("terminal-refresh").disabled = true;
  try {
    const result = await api("/api/terminal", {session: id, binding: session.binding});
    if (selected === id && generation === selectionGeneration) {
      $("terminal-text").textContent = result.text || "目前沒有文字內容。";
      if (threadVisible()) $("terminal-panel").scrollIntoView({block: "end"});
      if (!mobileLayout.matches) $("terminal-text").scrollTop = $("terminal-text").scrollHeight;
    }
  } catch (error) { if (selected === id) $("terminal-text").textContent = error.message; }
  finally { $("terminal-refresh").disabled = false; }
}
$("terminal-toggle").addEventListener("click", readTerminal);
$("attention-read").addEventListener("click", readTerminal);
$("progress-latest").addEventListener("click", () => {
  readLatestMessage();
});
$("terminal-refresh").addEventListener("click", readTerminal);
$("terminal-close").addEventListener("click", () => $("terminal-panel").hidden = true);
$("ack").addEventListener("click", async () => {
  const session = current(); if (!session) return; $("ack").disabled = true;
  try { await acknowledgeNotice(session.id, session.noticeID); }
  finally { $("ack").disabled = false; }
});
setInterval(poll, 2500);
document.addEventListener("visibilitychange", () => { if (!document.hidden) { window.PilotPush?.invalidate(); reportStyleHealth(); poll(); } });
window.addEventListener("pageshow", () => { window.PilotPush?.invalidate(); poll(); });
window.addEventListener("online", () => { window.PilotPush?.invalidate(); poll(); });
// All deferred feature modules must be ready before the first state response
// restores a route or picks a desktop conversation.
if (token) {
  if (document.readyState && document.readyState !== "complete") document.addEventListener("DOMContentLoaded", () => { if (token) showWorkspace(); }, {once: true});
  else showWorkspace();
}
