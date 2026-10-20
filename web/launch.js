"use strict";
(() => {
  let projects = [], defaultDirectory = "~", project = null, panel = "work";
  let submitting = false, attempt = null, pending = null, projectKey = "";
  let folderGeneration = 0, folder = null;
  let folderAttempt = null;
  let navigationGeneration = 0;
  function showPanel(value) {
    panel = value;
    $("work-list-panel").hidden = value !== "work";
    $("projects-panel").hidden = value !== "projects";
    $("launch-panel").hidden = value !== "launch";
    for (const [id, name] of [["tab-work", "work"], ["tab-projects", "projects"]]) {
      $(id).classList.toggle("active", value === name);
      $(id).setAttribute("aria-pressed", String(value === name));
    }
  }
  function launchOptions() {
    const agent = $("launch-tool").value !== "terminal";
    const specific = agent && $("launch-mode").value === "specific";
    $("launch-agent-options").hidden = !agent;
    $("launch-session-field").hidden = !specific;
    $("launch-mode").required = agent;
    $("launch-session").required = specific;
    $("launch-hint").textContent = !agent
      ? "只開 Terminal 並切到資料夾；手機可查看內容，shell 指令仍在電腦輸入。"
      : $("launch-tool").value === "codex"
        ? "沿用原本的 Codex Terminal 介面，自動接通手機核准與執行狀態。首次啟動或更新提示仍需在電腦完成。"
        : "沿用原本的 Claude Code。資料夾信任確認與文字回覆可在手機處理；其他首次設定與權限選單仍需在電腦操作。";
  }
  function openLauncher(saved = null) {
    if (submitting) return;
    project = saved; attempt = null; pending = null; folderGeneration++;
    folder = null; folderAttempt = null;
    $("directory-name").value = "";
    $("launch-title").textContent = saved ? "開啟專案" : "新工作";
    $("launch-name").value = saved?.name || "";
    $("launch-directory").value = saved?.directory || defaultDirectory;
    $("launch-tool").value = saved?.tool || "terminal";
    $("launch-mode").value = saved?.mode === "picker" ? "" : saved?.mode || "new";
    $("launch-session").value = saved?.sessionID || "";
    $("launch-pin").checked = Boolean(saved?.pinned);
    $("launch-name").readOnly = Boolean(saved);
    $("launch-directory").readOnly = Boolean(saved);
    $("launch-tool").disabled = Boolean(saved);
    $("browse-directory").hidden = Boolean(saved);
    $("launch-pin-label").hidden = Boolean(saved);
    $("directory-browser").hidden = true;
    $("launch-error").textContent = ""; $("launch-status").textContent = "";
    launchOptions(); showPanel("launch");
    if (mobileLayout.matches) window.scrollTo(0, 0);
  }
  function closeLauncher() {
    if (submitting) return;
    folderGeneration++; attempt = null;
    showPanel(project ? "projects" : "work");
  }
  function renderProjects() {
    const query = $("project-search").value.trim().toLocaleLowerCase();
    const key = JSON.stringify([projects, query]);
    if (projectKey === key) return;
    projectKey = key;
    const items = projects.filter(p => `${p.name} ${p.directory} ${p.toolName}`.toLocaleLowerCase().includes(query)).map(p => {
      const button = element("button", "session project");
      button.type = "button";
      button.append(element("div", "session-name", `${p.pinned ? "📌 " : ""}${p.name}`),
                    element("p", "session-path", `${p.toolName} · ${p.directory}`),
                    element("div", "session-bottom", "開啟專案 ›"));
      button.addEventListener("click", () => openLauncher(p));
      return button;
    });
    if (!items.length) items.push(element("p", "list-empty", projects.length ? "找不到符合的專案。" : "還沒有專案紀錄。開啟新工作後會自動保存，也會同步到 Mac。"));
    $("project-list").replaceChildren(...items);
  }
  async function browse(directory) {
    if (submitting) return;
    const generation = ++folderGeneration, activeToken = token;
    folder = null; folderAttempt = null; $("directory-browser").hidden = false;
    $("directory-create").hidden = true; $("directory-create-status").textContent = "";
    $("directory-note").textContent = "讀取電腦上的資料夾…";
    $("directory-items").replaceChildren();
    $("directory-use").disabled = true; $("directory-up").disabled = true; $("directory-new").disabled = true;
    try {
      const result = await api(`/api/directories?directory=${encodeURIComponent(directory || "~")}`);
      if (generation !== folderGeneration || token !== activeToken || panel !== "launch") return;
      folder = result;
      $("directory-current").textContent = result.directory;
      $("directory-use").disabled = false; $("directory-up").disabled = !result.parent;
      $("directory-new").disabled = false;
      const children = result.directories.map(child => {
        const button = element("button", "directory-item", `${child.name} ›`);
        button.type = "button"; button.addEventListener("click", () => browse(child.path));
        return button;
      });
      $("directory-items").replaceChildren(...children);
      $("directory-note").textContent = result.truncated ? "僅顯示前 300 個資料夾，也可直接輸入完整路徑。" : children.length ? "選擇子資料夾，或使用目前位置。" : "沒有可顯示的子資料夾。";
    } catch (error) {
      if (generation === folderGeneration && token === activeToken) $("directory-note").textContent = error.message;
    }
  }
  async function createFolder() {
    if (submitting || !token || !folder || project) return;
    const name = $("directory-name").value.trim();
    if (!name || [".", ".."].includes(name) || /[/\\\x00-\x1f\x7f]/.test($("directory-name").value)) {
      $("directory-create-status").textContent = "請輸入單一資料夾名稱，不能包含斜線、換行、. 或 ..。"; return;
    }
    const data = {parent: folder.directory, name}, fingerprint = JSON.stringify(data);
    if (folderAttempt?.fingerprint !== fingerprint) folderAttempt = {fingerprint, id: requestID()};
    const generation = folderGeneration, navigation = navigationGeneration, activeToken = token;
    busy(true, "folder"); $("directory-create-submit").textContent = "建立中…";
    $("directory-create-status").textContent = "";
    try {
      const result = await api("/api/directories", {...data, requestID: folderAttempt.id});
      if (token !== activeToken || generation !== folderGeneration || navigation !== navigationGeneration || panel !== "launch") return;
      if (!result.created || !result.directory) throw new Error("尚未確認是否建立成功。請重新按「選擇資料夾」查看；已存在時可直接選用，不會自動建立副本。");
      $("launch-directory").value = result.directory;
      if (!$("launch-name").value.trim()) $("launch-name").value = name;
      $("directory-browser").hidden = true; folderGeneration++; folderAttempt = null;
      $("launch-status").textContent = `已建立資料夾：${result.directory}，選好工作類型後即可開啟。`;
    } catch (error) {
      if (token === activeToken && generation === folderGeneration && navigation === navigationGeneration) {
        if (error.status === 409) folderAttempt = null;
        $("directory-create-status").textContent = error.name === "AbortError" || error instanceof TypeError
          ? "連線中斷，建立結果尚未確認。再按「建立並使用」會查核同一筆；也可重新讀取資料夾清單。" : error.message;
      }
    } finally {
      if (token === activeToken) { busy(false); $("directory-create-submit").textContent = "建立並使用"; }
    }
  }
  function busy(value, operation = "launch") {
    submitting = value; $("launch-fields").disabled = value;
    $("launch-submit").disabled = value; $("launch-cancel").disabled = value;
    $("new-work").disabled = value; $("tab-work").disabled = value; $("tab-projects").disabled = value;
    $("launch-submit").textContent = value && operation === "launch" ? "正在電腦開啟…" : "開啟工作";
  }
  $("new-work").addEventListener("click", () => openLauncher());
  $("launch-cancel").addEventListener("click", closeLauncher);
  $("launch-tool").addEventListener("change", launchOptions);
  $("launch-mode").addEventListener("change", launchOptions);
  $("project-search").addEventListener("input", renderProjects);
  for (const [id, value] of [["tab-work", "work"], ["tab-projects", "projects"]]) {
    $(id).addEventListener("click", () => { if (!submitting) { folderGeneration++; pending = null; showPanel(value); } });
  }
  $("browse-directory").addEventListener("click", () => browse($("launch-directory").value));
  $("directory-up").addEventListener("click", () => { if (folder?.parent) browse(folder.parent); });
  $("directory-new").addEventListener("click", () => {
    if (!folder || submitting) return;
    $("directory-create").hidden = false; $("directory-name").focus?.();
  });
  $("directory-create-cancel").addEventListener("click", () => {
    if (!submitting) { $("directory-create").hidden = true; folderAttempt = null; $("directory-create-status").textContent = ""; }
  });
  $("directory-create-submit").addEventListener("click", createFolder);
  $("directory-name").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); createFolder(); }
  });
  $("directory-use").addEventListener("click", () => {
    if (folder) { $("launch-directory").value = folder.directory; $("directory-browser").hidden = true; folderGeneration++; }
  });
  $("launch-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (submitting || !token) return;
    const tool = project?.tool || $("launch-tool").value;
    const data = project ? {projectID: project.id} : {tool, name: $("launch-name").value.trim(), directory: $("launch-directory").value.trim(), pinned: $("launch-pin").checked};
    if (tool !== "terminal") {
      data.mode = $("launch-mode").value;
      data.sessionID = data.mode === "specific" ? $("launch-session").value.trim() : "";
      if (!data.mode || (data.mode === "specific" && (!data.sessionID || data.sessionID.startsWith("-")))) {
        $("launch-error").textContent = "請選擇對話啟動方式，指定對話時需填入有效的 session ID 或名稱。"; return;
      }
    }
    const fingerprint = JSON.stringify(data);
    if (attempt?.fingerprint !== fingerprint) attempt = {fingerprint, id: requestID()};
    const activeToken = token, navigation = navigationGeneration;
    busy(true); $("launch-error").textContent = "";
    try {
      const result = await api("/api/launch", {...data, requestID: attempt.id});
      if (token !== activeToken) return;
      if (!result.opened || !result.binding) throw new Error("無法確認是否已開啟。請先查看工作列表，避免重複開啟；確認後可取消再建立新請求。");
      pending = navigation === navigationGeneration ? {binding: result.binding, tool: result.tool, token: activeToken, started: Date.now()} : null;
      folderGeneration++; attempt = null; showPanel("work");
      $("launch-status").textContent = "已在電腦開啟，正在連接新工作…";
      poll();
    } catch (error) {
      if (token === activeToken) $("launch-error").textContent = `${error.name === "AbortError" || error instanceof TypeError ? "連線中斷，無法確認是否已開啟；請先查看工作列表。" : error.message}`;
    } finally { if (token === activeToken) busy(false); }
  });
  window.PilotLaunch = {
    openProject(id) { const saved = projects.find(p => p.id === id); if (saved) openLauncher(saved); },
    update(state) {
      projects = state.projects || [];
      defaultDirectory = state.defaultDirectory || "~";
      renderProjects();
    },
    takeOpenedWork(sessions) {
      if (!pending || pending.token !== token) return "";
      const row = sessions.find(s => s.binding === pending.binding && s.tool === pending.tool);
      if (row) { pending = null; $("launch-status").textContent = "工作已開啟。"; return row.id; }
      if (Date.now() - pending.started > 15000) {
        pending = null; $("launch-status").textContent = "Terminal 視窗已開啟。Agent 可能仍在啟動或需要首次設定，可稍後從列表選擇。";
      }
      return "";
    },
    cancelNavigation() { navigationGeneration++; pending = null; },
    reset() {
      projects = []; project = null; projectKey = ""; pending = null; attempt = null; folder = null; folderAttempt = null; folderGeneration++;
      busy(false); showPanel("work"); $("project-list").replaceChildren(); $("launch-status").textContent = "";
      $("launch-name").value = ""; $("launch-directory").value = ""; $("launch-session").value = "";
      $("directory-name").value = ""; $("directory-create-status").textContent = "";
    }
  };
})();
