"use strict";
// No conversations, credentials or terminal snapshots are cached by this worker.
self.addEventListener("install", event => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

// Only app navigations get a public fallback. Authenticated API requests, actual
// pages and assets always go to the network and never enter a Cache.
const offlinePage = `<!doctype html>
<html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terminal Pilot</title>
<main><h1>Terminal Pilot</h1><p role="status">暫時無法開啟，正在重新連接服務…</p>
<button type="button">立即重連</button></main>
<script>let checking = false;
async function recover() {
  if (checking || document.hidden || navigator.onLine === false) return;
  checking = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch("/", {cache: "no-store", signal: controller.signal});
    if (response.ok) location.reload();
  } catch {} finally {
    clearTimeout(timeout);
    checking = false;
  }
}
document.querySelector("button").addEventListener("click", recover);
document.addEventListener("visibilitychange", recover);
window.addEventListener("online", recover);
navigator.serviceWorker.addEventListener("message", event => {
  if (event.data?.type !== "pilot-open" || typeof event.data.session !== "string") return;
  history.replaceState(null, "", "/?work=" + encodeURIComponent(event.data.session));
  recover();
});
setInterval(recover, 3000);</script></html>`;
self.addEventListener("fetch", event => {
  const {request} = event;
  if (request.mode !== "navigate" || request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname !== "/") return;
  event.respondWith((async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(request, {signal: controller.signal});
      if (response.status < 500) return response;
    } catch { /* The worker can still render its public fallback. */ }
    finally { clearTimeout(timeout); }
    return new Response(offlinePage, {headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; script-src 'sha256-fXavEiMj8uK1GvXepxvVAPiclnVjJDUgYzBegW01iSQ='; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
      "Referrer-Policy": "no-referrer"
    }});
  })());
});

async function badge(count) {
  try {
    if (count > 0 && self.navigator.setAppBadge) await self.navigator.setAppBadge(count);
    else if (self.navigator.clearAppBadge) await self.navigator.clearAppBadge();
  } catch { /* Badge permission can be disabled independently. */ }
}
// Push and page messages may overlap while OS notification APIs are awaiting.
// Commit them in arrival order so an older push cannot finish after a newer ack.
let updates = Promise.resolve();
function enqueue(update) {
  const result = updates.then(update);
  updates = result.catch(() => {});
  return result;
}
self.addEventListener("push", event => {
  event.waitUntil(enqueue(async () => {
    let data = {};
    try { data = event.data?.json() || {}; } catch { /* Still display a visible notification. */ }
    const session = typeof data.session === "string" ? data.session : "";
    const noticeID = typeof data.noticeID === "string" ? data.noticeID : "";
    const count = Number.isSafeInteger(data.count) && data.count >= 0 ? data.count : 1;
    // Apple requires a visible notification for EVERY push, even with a page open.
    await self.registration.showNotification(data.title || "Terminal Pilot", {
      body: data.body || "有工作需要你處理", icon: "/icon-192.png", tag: data.tag || "pilot-notice",
      silent: false, data: {session, noticeID}
    });
    await badge(count);
    const pages = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    pages.forEach(page => page.postMessage({type: "pilot-push"}));
  }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const session = event.notification.data?.session || "";
  event.waitUntil((async () => {
    const pages = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    const page = pages.find(item => new URL(item.url).origin === self.location.origin);
    if (page) {
      await page.focus();
      page.postMessage({type: "pilot-open", session});
    } else {
      await self.clients.openWindow(session ? `/?work=${encodeURIComponent(session)}` : "/");
    }
  })());
});
self.addEventListener("message", event => {
  if (event.data?.type !== "pilot-state" || !Array.isArray(event.data.notices)) return;
  event.waitUntil(enqueue(async () => {
    const notices = event.data.notices;
    await badge(notices.length);
    for (const notification of await self.registration.getNotifications()) {
      const data = notification.data;
      if (data?.session && !notices.some(item => item.session === data.session && item.noticeID === data.noticeID)) notification.close();
    }
  }));
});
