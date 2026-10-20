"use strict";
(() => {
  const panel = $("push-panel"), note = $("push-note"), enable = $("push-enable");
  const test = $("push-test"), disable = $("push-disable"), httpsLink = $("push-https");
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  let registration, subscription, busy = false, initialized = false, lastSync = 0, signature = "";
  const secure = window.isSecureContext && location.protocol === "https:";
  const capable = secure && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

  function controls(subscribed) {
    enable.hidden = subscribed; test.hidden = !subscribed; disable.hidden = !subscribed;
    enable.disabled = busy || !capable || (ios && !standalone);
    test.disabled = busy; disable.disabled = busy;
  }
  function guidance() {
    if (!secure) return "手機通知需要 HTTPS。先在 Mac 的「網頁」啟用 iPhone 通知，再使用 HTTPS 網址。";
    if (ios && !standalone) return "在 Safari 點「分享 → 加入主畫面」，再從 Terminal Pilot 圖示開啟，才能允許通知。";
    if (!capable) return "請使用 iOS 16.4 以上的 Safari，並將此網站加入主畫面。";
    if (Notification.permission === "denied") return "通知已關閉。請到 iPhone「設定 → 通知 → Terminal Pilot」允許通知。";
    return "開啟後，工作需要你時會收到通知與圖示徽章。聲音、震動依 iPhone 設定。";
  }
  async function setup() {
    if (!capable) return;
    registration = await navigator.serviceWorker.register("/sw.js", {scope: "/", updateViaCache: "none"});
    await navigator.serviceWorker.ready;
    subscription = await registration.pushManager.getSubscription();
    if (subscription && token) {
      const status = await api("/api/push/status", {endpoint: subscription.endpoint});
      // A reset pairing revokes server registrations. Require another explicit
      // enable action instead of silently restoring a revoked subscription.
      if (!status.subscribed) {
        await subscription.unsubscribe(); subscription = null;
      } else {
        note.textContent = status.error || "通知已開啟。可按「測試通知」，鎖定手機確認提示。";
      }
    }
    controls(Boolean(subscription));
    signature = "";
  }
  enable.addEventListener("click", async () => {
    if (busy || !capable || (ios && !standalone)) return;
    // Permission must be requested directly in this tap, before network awaits.
    const permission = Notification.requestPermission();
    busy = true; controls(false);
    try {
      if (await permission !== "granted") throw new Error("尚未允許通知，請到 iPhone 的通知設定開啟。 ");
      if (!registration) await setup();
      const config = await api("/api/push/config");
      const encoded = config.publicKey.replace(/-/g, "+").replace(/_/g, "/");
      const key = Uint8Array.from(atob(encoded + "=".repeat((4 - encoded.length % 4) % 4)), c => c.charCodeAt(0));
      subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: key});
      await api("/api/push/subscribe", {subscription: subscription.toJSON()});
      note.textContent = "通知已開啟。按「測試通知」後鎖定手機，確認聲音與震動。";
      signature = ""; update(sessions);
    } catch (error) {
      if (subscription) { try { await subscription.unsubscribe(); } catch {} subscription = null; }
      note.textContent = error.message || "未能啟用通知，請稍後再試。";
    } finally { busy = false; controls(Boolean(subscription)); }
  });
  async function unsubscribe() {
    if (!subscription && registration) subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      // Revoke server delivery first. Keep the controls available if it fails.
      await api("/api/push/unsubscribe", {endpoint: subscription.endpoint});
      await subscription.unsubscribe(); subscription = null;
    }
    if (navigator.clearAppBadge) await navigator.clearAppBadge().catch(() => {});
    note.textContent = "這台裝置的通知已關閉。"; controls(false);
  }
  disable.addEventListener("click", async () => {
    busy = true; controls(true);
    try { await unsubscribe(); } catch (error) { note.textContent = error.message; }
    finally { busy = false; controls(Boolean(subscription)); }
  });
  test.addEventListener("click", async () => {
    if (!subscription || busy) return;
    busy = true; controls(true); note.textContent = "5 秒後送出測試通知，現在可以鎖定手機。";
    try {
      // The server owns the delay, so iOS may suspend this page immediately.
      await api("/api/push/test", {endpoint: subscription.endpoint});
      note.textContent = "測試已交給 Apple 推播服務；請確認手機是否收到。";
    } catch (error) { note.textContent = error.message; }
    finally { busy = false; controls(Boolean(subscription)); }
  });
  function update(items, httpsURL = "") {
    const notices = items.filter(item => item.unread).map(item => ({session: item.id, noticeID: item.noticeID}));
    const next = JSON.stringify(notices);
    if (next !== signature) {
      signature = next;
      if (navigator.setAppBadge && notices.length) navigator.setAppBadge(notices.length).catch(() => {});
      else if (navigator.clearAppBadge) navigator.clearAppBadge().catch(() => {});
      registration?.active?.postMessage({type: "pilot-state", notices});
    }
    if (httpsURL && !secure) {
      try {
        const url = new URL(httpsURL);
        if (url.protocol === "https:" && url.hostname.endsWith(".ts.net")) { httpsLink.href = url.href; httpsLink.hidden = false; }
      } catch {}
    }
    if (!initialized && token) {
      initialized = true;
      setup().catch(error => { note.textContent = error.message; });
    }
    if (subscription && !busy && Date.now() - lastSync > 60000) {
      lastSync = Date.now();
      api("/api/push/status", {endpoint: subscription.endpoint}).then(status => {
        if (status.error) note.textContent = status.error;
        if (!status.subscribed) { note.textContent = "通知訂閱已失效，請重新啟用。"; controls(false); }
      }).catch(() => {});
    }
  }
  window.PilotPush = {update, unsubscribe};
  if ("serviceWorker" in navigator) navigator.serviceWorker.addEventListener("message", event => {
    if (event.data?.type === "pilot-open") openPushWork(event.data.session);
    else if (event.data?.type === "pilot-push" && token) poll();
  });
  panel.hidden = false; note.textContent = guidance(); controls(false);
  // Do not clear a badge using the initial empty array before Mac state arrives.
  if (token) { initialized = true; setup().catch(error => { note.textContent = error.message; }); }
})();
