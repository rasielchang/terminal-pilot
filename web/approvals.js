"use strict";
(() => {
  let key = "", pendingBinding = "";
  const attempts = new Map(), migrations = new Map();
  function inputFor(field, form) {
    const label = element("label", "approval-field"), title = field.title || field.name;
    label.append(element("span", "", `${title}${field.required ? "（必填）" : ""}`));
    if (field.description) label.append(element("small", "", field.description));
    let input;
    if (field.options || field.type === "boolean") {
      input = element("select");
      if (field.type === "array") input.multiple = true;
      else { const empty = element("option", "", "請選擇…"); empty.value = ""; input.append(empty); }
      const options = field.options || [{value: "true", label: "是"}, {value: "false", label: "否"}];
      for (const [index, option] of options.entries()) {
        const node = element("option", "", option.label); node.value = String(index); input.append(node);
      }
      input.readAnswer = () => {
        if (field.type === "array") return [...input.selectedOptions].map(o => options[Number(o.value)].value);
        if (input.value === "") return undefined;
        const value = options[Number(input.value)].value;
        return field.type === "boolean" ? value === "true" : value;
      };
    } else {
      input = element("input");
      input.type = field.secret ? "password" : ["number", "integer"].includes(field.type) ? "number" : field.format === "email" ? "email" : field.format === "date" ? "date" : "text";
      if (input.type === "number") { input.step = field.type === "integer" ? "1" : "any"; }
      for (const [from, to] of [["minLength", "minLength"], ["maxLength", "maxLength"], ["minimum", "min"], ["maximum", "max"]]) {
        if (field[from] != null) input[to] = field[from];
      }
      input.autocomplete = "off";
      input.readAnswer = () => input.value === "" && !field.required ? undefined : ["number", "integer"].includes(field.type) ? Number(input.value) : input.value;
    }
    input.required = Boolean(field.required);
    label.append(input); form.append(label);
    return input;
  }
  function renderCard(session, request, options = {}) {
    const card = element("section", "approval-card"), form = element("form", "approval-form");
    card.append(element("h3", "", request.title));
    if (request.server) card.append(element("p", "fine", `工具來源：${request.server}`));
    if (request.detail) card.append(element("p", "approval-detail", request.detail));
    for (const context of request.context || []) {
      const details = element("details", "approval-context");
      details.open = true;
      details.append(element("summary", "", context.label), element("pre", "", context.text)); card.append(details);
    }
    if (request.unsupported) card.append(element("p", "", request.unsupported));
    if (request.url) {
      const link = element("a", "secondary", "開啟工具確認頁面");
      link.href = request.url; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link);
    }
    const readers = [];
    if (request.kind === "question") {
      for (const q of request.questions) {
        const field = {name: q.id, title: q.question, type: "string", required: true, secret: q.isSecret};
        if (q.options?.length && !q.isOther) field.options = q.options.map(o => ({value: o.label, label: o.description ? `${o.label} — ${o.description}` : o.label}));
        else if (q.options?.length) field.description = q.options.map(o => `${o.label}：${o.description}`).join("\n");
        readers.push([q.id, inputFor(field, form)]);
      }
    } else for (const field of request.fields || []) readers.push([field.name, inputFor(field, form)]);
    const actions = element("div", "approval-actions"), status = element("p", "approval-status");
    status.setAttribute("role", "status");
    const attempt = options.attempt || attempts.get(request.id);
    const locked = !connected || request.state !== "pending" || Boolean(attempt && !attempt.rejected);
    if (attempt) status.textContent = attempt.message;
    else if (request.state === "submitting") status.textContent = "決定已送出，等待 agent 確認…";
    else if (request.state === "uncertain") status.textContent = "送出途中斷線，請等待重新連接確認結果。";
    else if (!connected) status.textContent = "連線中斷，重新連接後才能回覆。";
    const labels = {accept: request.kind === "url" ? "已完成確認" : "允許這次", decline: "拒絕", cancel: "取消這個要求", answer: "送出答案"};
    if (request.kind === "workspace_trust") { labels.accept = "同意並信任此資料夾"; labels.decline = "不同意並離開"; }
    if (request.kind === "startup_menu") Object.assign(labels, request.actionLabels || {});
    for (const action of request.actions) {
      const button = element("button", action === "accept" || action === "answer" ? "primary" : "secondary", labels[action]);
      button.type = "button"; button.disabled = locked;
      button.addEventListener("click", () => {
        if (action === "accept" || action === "answer") { if (!form.reportValidity()) return; }
        const content = Object.create(null);
        for (const [name, input] of readers) { const value = input.readAnswer(); if (value !== undefined) content[name] = value; }
        (options.submit || submit)(session, request, action, content);
      });
      actions.append(button);
    }
    form.addEventListener("submit", event => event.preventDefault());
    form.append(actions, status); card.append(form); return card;
  }
  async function submit(session, request, action, content) {
    if (!connected || (attempts.has(request.id) && !attempts.get(request.id).rejected) || current()?.id !== session.id || current()?.binding !== session.binding) return;
    const attempt = {id: requestID(), message: "正在送出你的決定…"};
    attempts.set(request.id, attempt); key = ""; update(current());
    try {
      const result = await api("/api/approval", {session: session.id, binding: session.binding,
        approval: request.id, requestID: attempt.id, action, content});
      attempt.message = result.resolved ? "已收到，要求已處理。" : result.uncertain ? "送出結果尚未確認，請等待狀態更新。" : "決定已送出，等待 agent 確認…";
    } catch (error) {
      attempt.rejected = error.status === 409;
      attempt.message = attempt.rejected ? error.message : `尚未確認送出結果：${error.message}`;
    }
    key = ""; update(current()); await poll();
  }
  function update(session) {
    const requests = session?.runtime?.approvals || [];
    const next = JSON.stringify([session?.id, session?.binding, connected, requests, requests.map(r => attempts.get(r.id)?.message)]);
    $("approval-panel").hidden = !requests.length;
    if (next !== key) { key = next; $("approval-panel").replaceChildren(...requests.map(r => renderCard(session, r))); }
    const migrate = session?.tool === "codex" && !session.codexSocket && session.codexThreadID && session.binding && session.phase !== "ended";
    $("approval-upgrade").hidden = !migrate;
    $("approval-upgrade-jump").hidden = !migrate;
    $("approval-upgrade-start").disabled = !connected || migrations.has(session?.id);
    $("approval-upgrade-status").textContent = migrations.get(session?.id)?.message || "";
  }
  async function upgrade() {
    const session = current();
    if (!connected || !session || migrations.has(session.id)) return;
    const attempt = {id: requestID(), message: "正在停止舊工作，保留並續接對話…"};
    migrations.set(session.id, attempt); update(session);
    try {
      const result = await api("/api/approval/upgrade", {session: session.id, binding: session.binding, requestID: attempt.id});
      if (result.opened) { pendingBinding = result.binding; attempt.message = "已續接，正在連接新工作…"; }
      else attempt.message = "結果尚未確認，請等工作列表更新。";
    } catch (error) { attempt.message = `續接尚未確認：${error.message}`; }
    update(current()); await poll();
  }
  $("approval-upgrade-start").addEventListener("click", upgrade);
  $("approval-upgrade-jump").addEventListener("click", () => { $("approval-upgrade").open = true; $("approval-upgrade").scrollIntoView({block: "start", behavior: "smooth"}); });
  window.PilotApprovals = {update, submit, renderCard, takeOpenedWork(list) {
    const work = pendingBinding && list.find(s => s.binding === pendingBinding && s.tool === "codex");
    if (work) { pendingBinding = ""; return work.id; } return "";
  }};
})();
