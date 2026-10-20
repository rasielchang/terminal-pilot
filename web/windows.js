"use strict";
(() => {
  let target = null, pending = false, previewing = false;
  const dialog = $("window-close-dialog");
  function update(session) {
    $("window-close").hidden = !session?.binding || session.canClose === false;
    $("window-close").disabled = !connected || pending || previewing;
    $("window-close-confirm").disabled = !connected || pending || !target;
    if (dialog.open && !pending && target && (session?.id !== target.session || session?.binding !== target.binding)) {
      target = null; dialog.close();
    }
  }
  async function preview() {
    const session = current();
    if (!connected || !session?.binding || pending || previewing) return;
    previewing = true; update(session);
    try {
      const result = await api("/api/terminal/close-preview", {session:session.id, binding:session.binding});
      if (current()?.id !== session.id || current()?.binding !== session.binding) return;
      target = {session:session.id, binding:session.binding, bindings:result.bindings};
      $("window-close-targets").replaceChildren(...result.targets.map(t => element("li", "", `${t.title} · ${t.status}`)));
      $("window-close-status").textContent = "";
      dialog.showModal();
      $("window-close-cancel").focus();
    } catch (error) { feedback.set(session.id, {text:error.message,error:true}); renderHeader(); }
    finally { previewing = false; update(current()); }
  }
  async function close() {
    if (!connected || pending || !target || current()?.id !== target.session || current()?.binding !== target.binding) return;
    const closing = {...target, requestID:requestID()};
    pending = true; update(current());
    $("window-close-status").textContent = "正在停止工作並關閉視窗…";
    try {
      const result = await api("/api/terminal/close", closing);
      if (!result.closed) throw new Error("尚未確認關閉結果，請重新整理工作列表。請勿重複關閉。");
      drafts.delete(closing.session); feedback.delete(closing.session);
      target = null; dialog.close(); replaceListRoute(); showSessionList();
      selected = ""; selectionGeneration++; $("message").value = ""; drafts.delete(closing.session);
      await poll();
    } catch (error) {
      target = null; // No automatic or double-tap retry after an ambiguous result.
      $("window-close-status").textContent = `關閉尚未完成：${error.message}`;
    } finally { pending = false; update(current()); }
  }
  dialog.addEventListener("cancel", event => { if (pending) event.preventDefault(); else target = null; });
  $("window-close").addEventListener("click", preview);
  $("window-close-confirm").addEventListener("click", close);
  $("window-close-cancel").addEventListener("click", () => { if (!pending) { target = null; dialog.close(); } });
  window.PilotWindows = {update};
})();
