"use strict";
// One assistant, independently durable matter conversations. Manager stays independent.
(() => {
  const validMode = value => ["assistant", "manager"].includes(value);
  let mode = history.state?.pilot?.mode || fragment.get("view");
  if (!validMode(mode)) { try { mode = localStorage.getItem("pilot-view"); } catch {} }
  if (!validMode(mode)) mode = "assistant";
  let state = null, generation = 0, navigation = 0, fetching = false;
  let scope = history.state?.pilot?.topic || fragment.get("topic") || "";
  const views = new Map();
  const view = (id = scope) => {
    if (!views.has(id)) views.set(id, {draft:"", attempt:null, submitting:false, error:"", scroll:null, state:null});
    return views.get(id);
  };
  let messageKey = "", topicsKey = "", sourceGeneration = 0, sourceJob = null, sourceScroll = 0;
  let creating = false, createAttempt = null, loadingEarlier = false;
  let rendered = 0, decisionKey = "", jobKey = "", initialScroll = true, lastAck = 0;
  let loadError = "", controlBusy = false;
  let opening = false, openAttempt = null;
  const decisions = new Map();
  const splitSource = () => sourceJob && window.innerWidth >= 1000;
  const scroller = () => mobileLayout.matches ? document.scrollingElement : splitSource() ? $("brain-conversation") : $("assistant-home");
  const atBottom = () => { const s = scroller(); return s.scrollHeight - s.scrollTop - s.clientHeight < 180; };
  const visible = () => mode === "assistant" && !document.hidden && !$("workspace").hidden && !sourceJob;
  const button = (text, action, cls = "text-button") => {
    const node = element("button", cls, text); node.type = "button"; node.addEventListener("click", action); return node;
  };
  // Install through the already served assistant script, so the live host does
  // not need a restart to refresh its cached HTML or stylesheet.
  const workMenu = (() => {
    const bar = document.querySelector?.(".mode-bar");
    if (!bar) return null;
    Object.assign(bar.style, {position:"sticky", top:"0", zIndex:"40", gap:"8px", gridColumn:"1 / -1", display:"flex", alignItems:"center", background:"#182724", color:"#e9eee9", padding:"max(10px, env(safe-area-inset-top)) 12px 10px"});
    const brand = bar.querySelector(".mode-brand"); if (brand) brand.hidden = true;
    for (const id of ["mode-assistant", "mode-manager"]) $(id).style.padding = "9px 8px";
    const trigger = button("選單", () => { updateWorkMenu(); dialog.showModal(); });
    trigger.id = "work-menu-open"; trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-controls", "work-menu");
    Object.assign(trigger.style, {color:"#24392f", background:"#d8e6d4", borderRadius:"8px", padding:"8px 10px", minHeight:"44px", flexShrink:"0", fontSize:"13px", lineHeight:"1.35", whiteSpace:"pre-line"});
    bar.append(trigger);
    const dialog = element("dialog", "brain-topic-picker"); dialog.id = "work-menu";
    dialog.setAttribute("aria-labelledby", "work-menu-title");
    const heading = element("div", "brain-picker-heading");
    const title = element("h2", "", "工作與選單"); title.id = "work-menu-title";
    heading.append(title, button("關閉", () => dialog.close()));
    const summary = element("p", "fine"); summary.id = "work-menu-status";
    const rows = element("div"); rows.id = "work-menu-sessions";
    const actions = element("div", "brain-topic-links");
    actions.append(button("所有工作 ↗", () => { dialog.close(); chooseMode("manager"); }),
                   button("接著聊的事情 ↗", () => { dialog.close(); if (mode !== "assistant") chooseMode("assistant"); showTopics(); }));
    dialog.append(heading, summary, rows, actions); $("workspace").append(dialog);
    dialog.addEventListener("click", event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
    return {bar, trigger, dialog, summary, rows, key:""};
  })();
  function workMenuLayout() {
    if (!workMenu) return;
    const workspace = $("workspace"), home = $("assistant-home");
    workMenu.bar.style.position = mode === "assistant" ? "fixed" : "static";
    workMenu.bar.style.left = mode === "assistant" ? "0" : "";
    workMenu.bar.style.right = mode === "assistant" ? "0" : "";
    workspace.style.paddingTop = mode === "assistant" ? workMenu.bar.getBoundingClientRect().height + "px" : "";
    workspace.style.gridTemplateRows = mode === "assistant" ? "minmax(0, 1fr)" : "auto minmax(0, 1fr)";
    workspace.style.gridTemplateColumns = mode === "assistant" ? "minmax(0, 1fr)" : "";
    home.style.gridColumn = "1 / -1"; home.style.minHeight = "0";
    home.style.overflow = mobileLayout.matches ? "visible" : "auto";
  }
  window.addEventListener("resize", workMenuLayout);
  function updateWorkMenu() {
    if (!workMenu) return;
    const waiting = s => s.phase === "waiting" || Boolean(s.runtime?.approvals?.length);
    const running = s => !waiting(s) && (s.phase === "running" || s.phase === "starting" || s.runtime?.state === "running" || s.turnProgress?.state === "active");
    const active = sessions.filter(running).length, pending = sessions.filter(waiting).length;
    workMenu.trigger.textContent = !connected ? "選單 · 離線" : `選單 · ${active} 執行${pending ? `\n${pending} 待決定` : ""}`;
    workMenu.trigger.setAttribute("aria-label", `開啟工作選單，共 ${sessions.length} 個工作，${active} 個執行中，${pending} 個等待決定${connected ? "" : "，連線中斷"}`);
    workMenuLayout();
    workMenu.summary.textContent = connected ? `共 ${sessions.length} 個工作 · ${active} 執行中 · ${pending} 等待決定` : "連線中斷，工作狀態可能不是最新。";
    const key = JSON.stringify([connected, sessions.map(s => [s.id, s.title, s.status, running(s), waiting(s)])]);
    if (key === workMenu.key) return;
    workMenu.key = key;
    const ordered = [...sessions].sort((a,b) => Number(waiting(b)) - Number(waiting(a)) || Number(running(b)) - Number(running(a)));
    workMenu.rows.replaceChildren(...ordered.map(s => {
      const row = button("", () => { workMenu.dialog.close(); openWork(s.id); }, "brain-topic-row");
      row.dataset.session = s.id;
      const name = element("strong", "", s.title || "工作");
      const detail = element("span", "fine", `${waiting(s) ? "需要你的決定" : running(s) ? "執行中" : s.status || "已保存"} · 點選查看`);
      Object.assign(name.style, {display:"block", overflowWrap:"anywhere"}); detail.style.display = "block";
      row.append(name, detail); return row;
    }));
    if (!ordered.length) workMenu.rows.append(element("p", "fine", "目前沒有工作。"));
  }
  function latest({initial = false} = {}) {
    const nav = navigation, readingTop = scroller().scrollTop;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (nav !== navigation) return;
      if (!visible()) { if (initial) initialScroll = true; return; }
      if (scroller().scrollTop === readingTop) {
        window.PilotReading.start($("brain-messages"), scroller()); acknowledge({latest: true});
      }
    }));
  }
  function applyMode() {
    workMenuLayout();
    const pushPanel = $("push-panel");
    if (pushPanel) $(mode === "assistant" ? "brain-notifications" : "push-manager-home")?.append(pushPanel);
    $("workspace").classList.toggle("assistant-mode", mode === "assistant");
    $("assistant-home").hidden = mode !== "assistant";
    $("messages").hidden = false;
    $("back-label").textContent = "工作列表"; $("back").setAttribute("aria-label", "回工作列表");
    for (const value of ["assistant", "manager"]) $("mode-" + value).setAttribute("aria-pressed", String(mode === value));
    document.title = `Terminal Pilot · ${mode === "assistant" ? "Brain" : "工作管理"}`;
    try { localStorage.setItem("pilot-view", mode); } catch {}
    render();
  }
  function route(push = false) {
    const pilot = {view:"list", mode:"assistant", topic:scope, listScrollTop:0};
    const url = `${location.pathname}#view=assistant${scope ? `&topic=${encodeURIComponent(scope)}` : ""}`;
    history[push ? "pushState" : "replaceState"]({pilot}, "", url);
  }
  function rememberView() {
    view().draft = $("brain-input").value;
    if (visible()) view().scroll = scroller().scrollTop;
  }
  function switchTopic(id = "", {historyChange = true, latest: fresh = false} = {}) {
    if (typeof id !== "string" || id.length > 80) return;
    rememberView(); closeSource(false); $("brain-topic-picker").close();
    navigation++; fetching = false; loadingEarlier = false; scope = id; state = fresh ? null : view().state;
    $("brain-input").value = view().draft;
    rendered = 0; messageKey = ""; jobKey = ""; decisionKey = ""; lastAck = 0; loadError = "";
    initialScroll = fresh || view().scroll === null;
    $("brain-messages").replaceChildren(); $("brain-decisions").replaceChildren(); $("brain-jobs").replaceChildren();
    render(); if (historyChange) route(true);
    const nav = navigation, savedScroll = fresh ? null : view().scroll;
    requestAnimationFrame(() => { if (nav === navigation && savedScroll !== null) scroller().scrollTop = savedScroll; });
    refresh();
  }
  function chooseMode(value) {
    if (!validMode(value)) return;
    navigation++; fetching = false; loadingEarlier = false;
    rememberView(); closeSource(false); window.PilotLaunch?.cancelNavigation(); showSessionList(); mode = value; listScrollTop = 0;
    applyMode(); replaceListRoute(); window.scrollTo(0, 0);
    if (value === "assistant") { route(); initialScroll = view().scroll === null; const saved = view().scroll; requestAnimationFrame(() => { if (visible() && saved !== null) scroller().scrollTop = saved; }); refresh(); }
  }
  async function openWork(id) {
    if (!sessions.some(s => s.id === id)) return;
    chooseMode("manager"); await selectSession(id, {latest: true});
  }
  function render() {
    updateWorkMenu();
    const {attempt: sendAttempt, submitting, error: sendError} = view();
    $("brain-home").hidden = !scope;
    $("brain-topic-heading").hidden = !scope;
    $("brain-topic-title").textContent = state?.topic?.title || "正在讀取這件事…";
    $("brain-topic-goal").textContent = state?.topic?.goal || "";
    const unreadMatters = state?.topics?.filter(topic => topic.unread).length || 0;
    $("brain-scope").textContent = `正在談：${scope ? state?.topic?.title || "這件事" : "主對話 · 統整與交代"}${unreadMatters ? ` · ${unreadMatters} 件未讀提醒` : ""} ▾`;
    $("brain-input").placeholder = scope ? "接著談這件事…" : "交代新事情，或安排目前的工作…";
    $("brain-reference").hidden = !view().source;
    $("brain-reference-label").textContent = view().source ? `引用回報：${view().sourceTitle || '原始交辦'}` : '';
    $("brain-earlier").hidden = !state?.hasMore;
    $("brain-earlier").disabled = loadingEarlier;
    $("brain-open").disabled = !connected || opening;
    $("brain-open").textContent = opening ? "正在開啟 Brain…" : state?.backend?.connected ? "查看 Brain 對話 ↗" : "開啟／續接 Brain Codex";
    $("brain-send").disabled = !connected || submitting || !$("brain-input").value.trim();
    $("brain-send").textContent = submitting ? "送出中…" : sendAttempt ? "確認送出 ↑" : "送出 ↑";
    $("brain-pause").disabled = !connected || controlBusy || !state;
    $("brain-pause").textContent = state?.paused ? "恢復交辦" : "暫停交辦";
    $("brain-error").textContent = sendError || loadError || state?.error || "";
    if (!state) { $("brain-welcome").hidden = Boolean(scope); $("brain-status").textContent = connected ? "正在連接 Brain…" : "正在重新連接服務…"; return; }
    renderTopics();
    renderStatus();
    $("brain-memory").textContent = state.memory.available ? `Brain 工作位置：${state.memory.path}` : "請先在電腦建立 ~/brain，作為 Brain 的工作位置。";
    const follow = initialScroll || atBottom();
    const previousLatest = window.PilotReading.latest($("brain-messages"))?.dataset.messageId;
    const readingTop = scroller().scrollTop;
    const nextKey = JSON.stringify(state.messages);
    const changed = messageKey !== nextKey;
    if (changed) { messageKey = nextKey; $("brain-messages").replaceChildren(); rendered = 0; }
    for (const message of changed ? state.messages : []) {
      const article = element("article", `message ${message.role} brain-${message.kind}`);
      article.dataset.messageId = String(message.id);
      article.dataset.readingTarget = String(message.role !== "system" && message.kind !== "system");
      const label = element("p", "message-label", message.role === "user" ? "你" : message.kind === "archive" ? "Agent 原始回報 · 轉入紀錄" : message.kind === "system" ? "協作紀錄" : "Brain");
      label.append(element("time", "", new Date(message.created * 1000).toLocaleTimeString("zh-TW", {hour: "2-digit", minute: "2-digit"})));
      article.append(label, renderText(message.text));
      if (message.links?.length) {
        const links = element("div", "brain-topic-links");
        for (const id of message.links) {
          const topic = state.topics?.find(t => t.id === id);
          if (topic) links.append(button(`${topic.title} ↗`, () => switchTopic(id)));
        }
        article.append(links);
      }
      $("brain-messages").append(article); rendered = Math.max(rendered, message.id);
    }
    $("brain-welcome").hidden = Boolean(scope || state.messages.length);
    renderDecisions(); renderJobs();
    if (changed) scroller().scrollTop = readingTop;
    if (state.messages.length && (initialScroll || changed && follow && previousLatest !== undefined &&
        previousLatest !== window.PilotReading.latest($("brain-messages"))?.dataset.messageId) && visible()) {
      const initial = initialScroll; initialScroll = false; latest({initial});
    }
  }
  // Receipt elapsed time, not CPU time or a claim of uninterrupted model activity.
  function renderStatus() {
    if (!state) return;
    const active = state.jobs.filter(j => ["queued", "dispatching", "sent", "running", "observing", "waiting", "uncertain"].includes(j.state));
    const pending = [];
    for (const message of state.messages) {
      if (message.role === "assistant" && ["result", "error"].includes(message.kind)) pending.length = 0;
      if (message.role === "user" && Number.isFinite(message.created) && message.created > 0) pending.push(message.created);
    }
    const starts = [
      ...(state.working ? pending : []),
      ...active.map(j => j.sentAt || j.created)
    ].filter(t => Number.isFinite(t) && t > 0);
    const seconds = starts.length ? Math.max(0, Math.floor(Date.now() / 1000 - Math.min(...starts))) : null;
    const duration = seconds === null ? "" : seconds < 3600
      ? `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, "0")} 秒`
      : `${Math.floor(seconds / 3600)} 小時 ${Math.floor(seconds % 3600 / 60)} 分 ${String(seconds % 60).padStart(2, "0")} 秒`;
    const elapsed = duration ? ` · 本次交代已經過 ${duration}` : "";
    const observed = view().observedAt;
    const stale = observed && Date.now() - observed > 15000;
    let text;
    if (!connected || loadError || stale) text = "連線或狀態更新中斷，目前無法確認進度";
    else if (state.decisions.length) text = "等待你處理確認卡片" + elapsed;
    else if (state.paused) text = "已暫停後續交辦，已執行的工作可能仍在進行" + elapsed;
    else if (state.working) text = (state.backend?.detail || "Brain 正在處理") + elapsed;
    else if (active.length) text = `${active.length} 件交辦正在追蹤` + elapsed;
    else text = state.error ? "目前需要處理錯誤，請查看提示" : "目前沒有執行中的交代，隨時可以找我。";
    const node = $("brain-status");
    if (node.textContent !== text) node.textContent = text;
    node.title = "時間從目前交代送達服務時計算，包含排隊、執行及等待確認；不是完成時間預估。";
  }
  // Updating only the status preserves drafts, scroll position and approval forms.
  if (typeof window.setInterval === "function") window.setInterval(() => {
    if (visible()) renderStatus();
  }, 1000);
  function renderTopics() {
    const topics = state.topics || [];
    const key = JSON.stringify([topics, scope]);
    if (key === topicsKey) return; topicsKey = key;
    $("brain-topic-list").replaceChildren(...topics.map(topic => {
      const row = button(topic.title, () => switchTopic(topic.id), "brain-topic-row");
      row.setAttribute("aria-current", String(topic.id === scope));
      const label = topic.unread ? "有新回覆" : topic.needsDecision ? "需要你的決定" : topic.working ? "正在處理" : topic.state === "done" ? "已完成 · 仍可接著聊" : "接著聊";
      row.append(element("small", topic.unread ? "brain-unread" : "", label));
      return row;
    }));
    const updates = topics.filter(t => t.id !== scope && t.unread);
    $("brain-updates").hidden = !updates.length;
    $("brain-updates").replaceChildren(...updates.map(t => button(`${t.title} · 有新回覆 ↗`, () => switchTopic(t.id))));
    $("brain-topics-open").textContent = topics.length ? `接著聊哪件事 · ${topics.length} ↗` : "接著聊哪件事 ↗";
  }
  function showTopics() {
    if (!$("brain-topic-picker").open) $("brain-topic-picker").showModal();
  }
  async function createTopic(event) {
    event.preventDefault();
    const title = $("brain-topic-name").value.trim();
    if (!title || creating || !connected) return;
    if (createAttempt && createAttempt.title !== title) { $("brain-topic-error").textContent = "上一件事建立結果未確認，請先重試原名稱。"; return; }
    creating = true; $("brain-topic-create").disabled = true;
    createAttempt ||= {requestID:requestID(), title};
    const epoch = generation;
    try {
      const result = await api("/api/brain/topics", createAttempt);
      if (generation !== epoch) return;
      createAttempt = null; $("brain-topic-name").value = ""; $("brain-topic-error").textContent = "";
      switchTopic(result.topic.id);
    } catch (error) {
      if (generation === epoch) {
        if (error.status === 409) createAttempt = null;
        $("brain-topic-error").textContent = error.status === 409 ? error.message : "尚未確認是否建立，再按建立會確認同一件事。";
      }
    } finally { if (generation === epoch) { creating = false; $("brain-topic-create").disabled = false; } }
  }
  async function earlier() {
    if (!state?.hasMore || loadingEarlier || !connected) return;
    loadingEarlier = true; render(); const nav = navigation, epoch = generation;
    const before = state.messages[0]?.id;
    try {
      const result = await api(`/api/brain?topic=${encodeURIComponent(scope)}&before=${before}`);
      if (nav !== navigation || epoch !== generation) return;
      const scroll = scroller(), height = scroll.scrollHeight, top = scroll.scrollTop;
      const known = new Set(state.messages.map(m => m.id));
      state.messages = [...result.messages.filter(m => !known.has(m.id)), ...state.messages]; state.hasMore = result.hasMore;
      initialScroll = false; render();
      requestAnimationFrame(() => { if (nav === navigation) scroll.scrollTop = top + scroll.scrollHeight - height; });
    } catch (error) { if (nav === navigation && epoch === generation) loadError = error.message; }
    finally { if (epoch === generation) { loadingEarlier = false; render(); } }
  }
  function closeSource(restore = true) {
    if (splitSource()) sourceScroll = $("brain-conversation").scrollTop;
    sourceGeneration++; sourceJob = null;
    $("brain-source").hidden = true; $("assistant-home").classList.toggle("source-open", false);
    $("brain-source-messages").replaceChildren();
    if (restore) { const nav = navigation; requestAnimationFrame(() => { if (nav === navigation) scroller().scrollTop = sourceScroll; }); }
  }
  async function openSource(job) {
    if (!connected) return;
    sourceScroll = scroller().scrollTop; sourceJob = job;
    const seq = ++sourceGeneration, epoch = generation, activeToken = token;
    $("brain-source").hidden = false; $("assistant-home").classList.toggle("source-open", true);
    $("brain-source-title").textContent = job.title;
    $("brain-source-status").textContent = "正在讀取原始內容…";
    $("brain-source-messages").replaceChildren();
    $("brain-source").scrollTop = 0;
    if (splitSource()) { $("assistant-home").scrollTop = 0; $("brain-conversation").scrollTop = sourceScroll; window.scrollTo(0, 0); }
    if (mobileLayout.matches) scroller().scrollTop = 0;
    try {
      const source = await api(`/api/brain/source?job=${encodeURIComponent(job.id)}`);
      if (seq !== sourceGeneration || epoch !== generation || token !== activeToken) return;
      $("brain-source-status").textContent = source.available ? "已保存的原始回報；下方可展開目前讀取到的近期對話。" : "原視窗已關閉或無法讀取，以下是已保存的內容。";
      const body = $("brain-source-messages");
      body.append(element("h3", "", "這次交辦"), renderText(source.instruction));
      if (source.result) body.append(element("h3", "", "原始回報"), renderText(source.result));
      else body.append(element("p", "fine", "尚未收到正式完成回報。"));
      body.append(button("針對這份回報問 Brain", () => {
        closeSource();
        view().source = job.id;
        view().sourceTitle = job.title;
        const title = `關於「${job.title}」這份回報，`;
        if (!$("brain-input").value.trim()) $("brain-input").value = title;
        view().draft = $("brain-input").value; render(); $("brain-input").focus();
      }));
      if (source.messages.length) {
        const details = element("details", "brain-work");
        details.append(element("summary", "", `查看近期對話 · ${source.messages.length} 則`));
        for (const message of source.messages) {
          const article = element("article", `message ${message.role}`);
          article.append(element("p", "message-label", message.role === "user" ? "你／Brain 交辦" : "Agent"), renderText(message.text));
          details.append(article);
        }
        body.append(details);
      }
    } catch (error) { if (seq === sourceGeneration && epoch === generation) $("brain-source-status").textContent = error.message; }
  }
  function renderJobs() {
    const key = JSON.stringify(state.jobs);
    if (key === jobKey) return; jobKey = key;
    $("brain-work").hidden = !state.jobs.length;
    $("brain-work-summary").textContent = `交辦紀錄 · ${state.jobs.length}`;
    const rows = state.jobs.slice().reverse().map(job => {
      const row = element("div", "brain-job"), label = element("p", "", `${job.title} · ${job.label}`);
      row.append(label);
      if (job.detail && job.state !== "completed") row.append(element("p", "fine", job.detail));
      const actions = element("div", "brain-job-actions");
      actions.append(button("讀取原始內容", () => openSource(job)));
      if (!scope && job.topic) actions.append(button("接著談這件事", () => switchTopic(job.topic)));
      if (sessions.some(s => s.id === job.session)) actions.append(button("操作工作視窗 ↗", () => openWork(job.session)));
      if (["queued", "dispatching", "sent", "running", "observing", "waiting", "uncertain"].includes(job.state)) actions.append(button("自己接手", () => control("takeover", job.id)));
      row.append(actions); return row;
    });
    $("brain-jobs").replaceChildren(...rows);
  }
  function renderDecisions() {
    const key = JSON.stringify([state.decisions.map(item => [item.session.id, item.session.binding, item.session.title, item.requests]), connected, [...decisions]]);
    if (key === decisionKey) return; decisionKey = key;
    $("brain-decisions").hidden = !state.decisions.length;
    $("brain-decisions").replaceChildren(...state.decisions.map(item => {
      const box = element("section", "brain-decision"), s = item.session;
      box.append(element("p", "eyebrow", `${s.title} · 需要你的決定`));
      for (const request of item.requests) box.append(window.PilotApprovals.renderCard(s, request, {submit: approve, attempt: decisions.get(request.id)}));
      if (!item.requests.length) box.append(element("p", "", "這個工作正在等待確認，但目前未接通手機核准。請查看原工作提示；Claude 或系統權限需在電腦處理。"));
      box.append(button("查看原始工作", () => openWork(s.id))); return box;
    }));
  }
  async function refresh() {
    if (!token || !connected || fetching || document.hidden || mode !== "assistant") return;
    fetching = true; const epoch = generation, nav = navigation, activeToken = token, target = scope;
    try {
      const result = await api(`/api/brain${target ? `?topic=${encodeURIComponent(target)}` : ""}`);
      if (epoch !== generation || token !== activeToken || nav !== navigation) return;
      // Keep pages already loaded; normal polling only replaces the newest page.
      const older = state?.messages.filter(m => m.id < (result.messages[0]?.id || 0)) || [];
      state = {...result, messages:[...older, ...result.messages], hasMore: older.length ? state.hasMore : result.hasMore};
      view().state = state; view().observedAt = Date.now(); loadError = ""; render();
    } catch (error) { if (epoch === generation && token === activeToken && nav === navigation) { loadError = `Brain 尚未更新：${error.message}`; render(); } }
    finally { if (epoch === generation && nav === navigation) fetching = false; }
  }
  async function send(text) {
    text = text.trim(); const target = scope, entry = view();
    if (!connected || entry.submitting || !text || !token) return;
    const epoch = generation, activeToken = token;
    if (entry.attempt && entry.attempt.text !== text) { entry.error = "上一筆送出結果還沒確認，先按「確認送出」；內容已保留。"; render(); return; }
    entry.attempt ||= {requestID: requestID(), text, ...(target ? {topic:target} : {}), ...(entry.source ? {source:entry.source} : {})};
    entry.submitting = true; entry.error = ""; loadError = ""; render();
    try {
      await api("/api/brain/message", entry.attempt);
      if (epoch !== generation || token !== activeToken) return;
      if (target === scope) entry.draft = $("brain-input").value;
      if (entry.draft.trim() === text) entry.draft = "";
      entry.attempt = null; entry.source = null;
      if (target === scope) { $("brain-input").value = entry.draft; initialScroll = true; await refresh(); }
    } catch (error) {
      if (epoch !== generation || token !== activeToken) return;
      if (error.status === 409) entry.attempt = null;
      entry.error = error.status === 409 ? error.message : "連線中斷，尚未確認是否收到。再按「確認送出」會核對同一筆，不會重複交辦。";
    } finally { if (epoch === generation) { entry.submitting = false; render(); } }
  }
  async function control(action, job) {
    if (controlBusy || !connected) return;
    controlBusy = true; render(); const epoch = generation;
    try { await api("/api/brain/control", {requestID: requestID(), action, ...(job ? {job} : {})}); await refresh(); }
    catch (error) { if (epoch === generation) loadError = error.message; }
    finally { if (epoch === generation) { controlBusy = false; render(); } }
  }
  async function approve(session, request, action, content) {
    const current = state?.decisions.find(item => item.session.id === session.id && item.session.binding === session.binding);
    if (!connected || !current?.requests.some(r => r.id === request.id) || (decisions.has(request.id) && !decisions.get(request.id).rejected)) return;
    const epoch = generation, attempt = {id: requestID(), message: "正在送出你的決定…"};
    decisions.set(request.id, attempt); renderDecisions();
    try {
      const result = await api("/api/approval", {session: session.id, binding: session.binding, approval: request.id, requestID: attempt.id, action, content});
      attempt.message = result.resolved ? "已收到你的決定。" : result.uncertain ? "送出結果待確認，請等待更新。" : "已送出，等待 agent 確認…";
    } catch (error) { attempt.rejected = error.status === 409; attempt.message = error.message; }
    if (epoch === generation) { renderDecisions(); await refresh(); }
  }
  async function acknowledge({latest = false} = {}) {
    if (!visible() || !connected || !rendered || rendered <= lastAck || (!latest && !atBottom())) return;
    const through = rendered, nav = navigation; lastAck = through;
    try { await api("/api/brain/ack", {through, ...(scope ? {topic:scope} : {})}); }
    catch { if (nav === navigation && lastAck === through) lastAck = 0; }
  }
  $("brain-home").addEventListener("click", () => switchTopic(""));
  $("brain-picker-home").addEventListener("click", () => switchTopic(""));
  $("brain-topics-open").addEventListener("click", showTopics);
  $("brain-scope").addEventListener("click", showTopics);
  $("brain-topics-close").addEventListener("click", () => $("brain-topic-picker").close());
  $("brain-new-topic").addEventListener("submit", createTopic);
  $("brain-earlier").addEventListener("click", earlier);
  $("brain-source-close").addEventListener("click", () => closeSource());
  $("brain-reference-clear").addEventListener("click", () => { view().source = null; render(); });
  $("brain-compose").addEventListener("submit", event => { event.preventDefault(); send(view().attempt?.text || $("brain-input").value); });
  $("brain-input").addEventListener("input", () => { view().draft = $("brain-input").value; render(); });
  $("brain-input").addEventListener("keydown", event => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); send(view().attempt?.text || $("brain-input").value); } });
  $("brain-catchup").addEventListener("click", () => send("請整理目前各個專案正在做什麼、有哪些事需要我決定，先回報，不要派送新的工作。"));
  $("brain-pause").addEventListener("click", () => control(state?.paused ? "resume" : "pause"));
  $("brain-open").addEventListener("click", async () => {
    if (opening || !connected) return;
    if (state?.backend?.connected && sessions.some(s => s.id === state.backend.session)) return openWork(state.backend.session);
    const activeToken = token, epoch = generation;
    opening = true; openAttempt ||= {requestID: requestID()}; render();
    try {
      const result = await api("/api/brain/open", openAttempt);
      if (activeToken !== token || epoch !== generation) return;
      if (result.opened) openAttempt = null;
      await refresh();
    } catch (error) { if (activeToken === token && epoch === generation) view().error = error.message; }
    finally { if (activeToken === token && epoch === generation) { opening = false; render(); } }
  });
  $("brain-latest").addEventListener("click", latest);
  $("assistant-home").addEventListener("scroll", acknowledge, {passive: true});
  window.addEventListener("scroll", acknowledge, {passive: true});
  $("mode-assistant").addEventListener("click", () => chooseMode("assistant"));
  $("mode-manager").addEventListener("click", () => chooseMode("manager"));
  window.PilotAssistant = {
    mode: () => mode, topic: () => scope, chooseMode, openWork, switchTopic, refresh: render,
    selection() { $("messages").hidden = false; },
    briefVisible: () => false, positionLatest: () => false, thread() {},
    restore(route) {
      if (validMode(route?.mode)) mode = route.view === "thread" ? "manager" : route.mode;
      if (mode === "assistant" && (route?.topic || "") !== scope) switchTopic(route?.topic || "", {historyChange:false});
      applyMode();
    },
    update() { render(); refresh(); },
    reset() {
      workMenu?.dialog.close(); if (workMenu) { workMenu.rows.replaceChildren(); workMenu.key = ""; }
      generation++; navigation++; state = null; fetching = false; views.clear(); scope = ""; controlBusy = false; closeSource(false);
      rendered = 0; lastAck = 0; initialScroll = true; decisionKey = ""; jobKey = ""; loadError = ""; messageKey = ""; topicsKey = ""; creating = false; createAttempt = null;
      opening = false; openAttempt = null;
      $("brain-topic-picker").close(); $("brain-topic-list").replaceChildren(); $("brain-updates").replaceChildren();
      $("brain-topic-title").textContent = ""; $("brain-topic-goal").textContent = "";
      $("brain-topic-name").value = ""; $("brain-topic-error").textContent = "";
      decisions.clear(); $("brain-input").value = ""; $("brain-messages").replaceChildren();
      $("brain-decisions").replaceChildren(); $("brain-jobs").replaceChildren();
    }
  };
  applyMode();
  if (history.state?.pilot?.view === "thread") recordThreadRoute(history.state.pilot.session);
  else { replaceListRoute(); if (mode === "assistant") route(); }
})();
