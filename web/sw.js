"use strict";
// No conversations, credentials or terminal snapshots are cached by this worker.
self.addEventListener("install", event => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

async function badge(count) {
  try {
    if (count > 0 && self.navigator.setAppBadge) await self.navigator.setAppBadge(count);
    else if (self.navigator.clearAppBadge) await self.navigator.clearAppBadge();
  } catch { /* Badge permission can be disabled independently. */ }
}
self.addEventListener("push", event => {
  event.waitUntil((async () => {
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
  })());
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
  event.waitUntil((async () => {
    const notices = event.data.notices;
    await badge(notices.length);
    for (const notification of await self.registration.getNotifications()) {
      const data = notification.data;
      if (data?.session && !notices.some(item => item.session === data.session && item.noticeID === data.noticeID)) notification.close();
    }
  })());
});
