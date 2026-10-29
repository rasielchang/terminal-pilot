"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const {browser} = require("./test_navigation.js");
// Drain all queued microtasks, including window and worker promise chains.
const flush = () => new Promise(resolve => setImmediate(resolve));
const source = name => fs.readFileSync(path.join(__dirname, "../web/", name), "utf8");

(async () => {
  const handlers = {}, shown = [], badges = [], opened = [], messages = [];
  let focused = 0, closed = 0, pages = [], existing = [];
  const worker = {
    addEventListener(name, callback) { handlers[name] = callback; },
    navigator: {setAppBadge: async count => badges.push(count), clearAppBadge: async () => badges.push(0)},
    location: {origin: "https://mac.example.ts.net:8443"},
    registration: {showNotification: async (title, options) => shown.push({title, options}), getNotifications: async () => existing},
    clients: {matchAll: async () => pages, openWindow: async url => opened.push(url)},
  };
  vm.runInNewContext(source("sw.js"), {self: worker, URL});
  async function dispatch(name, data) {
    let completion;
    handlers[name]({...data, waitUntil(value) { completion = value; }});
    await completion;
  }
  await dispatch("push", {data: {json: () => ({title: "專案", body: "需要你", session: "a", noticeID: "n1", count: 2, tag: "pilot-a"})}});
  assert.equal(shown.length, 1); assert.equal(shown[0].options.silent, false); assert.equal(badges.at(-1), 2);
  // Even malformed or foreground deliveries must display a visible notification on iOS.
  pages = [{url: "https://mac.example.ts.net:8443/", focus: async () => focused++, postMessage: value => messages.push(value)}];
  await dispatch("push", {data: {json() { throw new Error("invalid"); }}});
  assert.equal(shown.length, 2); assert.equal(messages.at(-1).type, "pilot-push");
  await dispatch("notificationclick", {notification: {data: {session: "a / 中文"}, close() {}}});
  assert.equal(focused, 1); assert.equal(messages.at(-1).session, "a / 中文"); assert.equal(opened.length, 0);
  pages = [];
  await dispatch("notificationclick", {notification: {data: {session: "a / 中文"}, close() {}}});
  assert.equal(opened[0], "/?work=a%20%2F%20%E4%B8%AD%E6%96%87");
  existing = [
    {data: {session: "a", noticeID: "old"}, close() { closed++; }},
    {data: {session: "a", noticeID: "new"}, close() { closed++; }},
  ];
  await dispatch("message", {data: {type: "pilot-state", notices: [{session: "a", noticeID: "new"}]}});
  assert.equal(closed, 1); assert.equal(badges.at(-1), 1);
  await dispatch("message", {data: {type: "pilot-state", notices: []}});
  assert.equal(badges.at(-1), 0);

  // An OS display operation can finish after a newer read-state message arrives.
  let finishDisplay;
  worker.registration.showNotification = () => new Promise(resolve => finishDisplay = resolve);
  const pendingPush = dispatch("push", {data: {json: () => ({session:"a", noticeID:"late", count:1})}});
  await flush();
  const pendingRead = dispatch("message", {data:{type:"pilot-state", notices:[]}});
  finishDisplay();
  await Promise.all([pendingPush, pendingRead]);
  assert.equal(badges.at(-1), 0, 'An earlier push finished after the acknowledgment and restored its badge');

  const app = browser();
  await app.run('token="paired"; api=async () => ({sessions:[{id:"a"},{id:"b"}]}); openPushWork("b");');
  await flush();
  assert.equal(app.run("selected"), "b");
  assert.equal(app.history.state.pilot.session, "b");
  app.history.back(); await flush();
  assert.equal(app.history.state.pilot.view, "list");

  const nodes = new Map(), calls = [], badgeUpdates = [], stateMessages = [], serviceEvents = {};
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {hidden: false, handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; }});
    return nodes.get(id);
  }
  let sub = null;
  const registration = {pushManager: {
    getSubscription: async () => sub,
    subscribe: async options => { calls.push("subscribe"); assert.equal(options.userVisibleOnly, true);
      return sub = {endpoint: "https://web.push.apple.com/test", toJSON: () => ({}), unsubscribe: async () => { sub = null; }}; }
  }, active: {postMessage(value) { stateMessages.push(value); }}};
  const context = {
    $: node, token: "paired", sessions: [], location: {protocol: "https:"},
    window: {isSecureContext: true, matchMedia: () => ({matches: true}), PushManager: {}, Notification: {}},
    navigator: {userAgent: "iPhone", serviceWorker: {register: async () => registration, ready: Promise.resolve(), addEventListener(name, fn) { serviceEvents[name] = fn; }},
      setAppBadge: async count => badgeUpdates.push(count), clearAppBadge: async () => badgeUpdates.push(0)},
    Notification: {permission: "default", requestPermission() { calls.push("permission"); return Promise.resolve("granted"); }},
    api: async route => { calls.push(route); if (route === "/api/push/config") return {publicKey: "BA"}; return {subscribed: true}; },
    atob, Uint8Array, URL, Date, openPushWork() {}, poll() {}
  };
  vm.runInNewContext(source("pwa.js"), context);
  await flush();
  assert.equal(badgeUpdates.length, 0, "Never clear badge before receiving real state");
  const enablePromise = node("push-enable").handlers.click();
  assert.equal(calls[0], "permission", "Safari permission requested synchronously in tap");
  await enablePromise;
  assert.deepEqual(calls.slice(0, 4), ["permission", "/api/push/config", "subscribe", "/api/push/subscribe"]);
  assert.equal(node("push-test").hidden, false);
  context.window.PilotPush.update([]); await flush();
  badgeUpdates.push(1); // A late push changed the icon; the authoritative state is still empty.
  context.poll = () => context.window.PilotPush.update([]);
  serviceEvents.message({data:{type:'pilot-push'}}); await flush();
  assert.equal(badgeUpdates.at(-1), 0, 'Same empty server state must reconcile a late push');
  assert.equal(stateMessages.at(-1).notices.length, 0);
  badgeUpdates.push(2);
  serviceEvents.controllerchange(); await flush();
  assert.equal(badgeUpdates.at(-1), 0, 'New service worker must receive the current read state');
  badgeUpdates.push(3);
  context.window.PilotPush.invalidate();
  context.window.PilotPush.update([]); await flush();
  assert.equal(badgeUpdates.at(-1), 0, 'Resume must reconcile the icon even if the notice list did not change');
  await node("push-test").handlers.click();
  assert.ok(node("push-note").textContent.includes("交給 Apple"));
  context.window.PilotPush.update([]); await flush();
  assert.equal(badgeUpdates.at(-1), 0);
  // A late push can set the system badge after the notice was already read.
  badgeUpdates.push(1);
  const reconciliations = stateMessages.length;
  context.window.PilotPush.update([]); await flush();
  assert.equal(badgeUpdates.at(-1), 0, "Unchanged authoritative state must clear a stale system badge");
  assert.ok(stateMessages.length > reconciliations, "The worker must receive unchanged read state too");
  context.window.PilotPush.update([
    {id:"brain:one", noticeID:"1", unread:true},
    {id:"brain:two", noticeID:"2", unread:true}
  ]); await flush();
  assert.equal(badgeUpdates.at(-1), 2);
  context.window.PilotPush.update([
    {id:"brain:one", noticeID:"1", unread:false},
    {id:"brain:two", noticeID:"2", unread:true}
  ]); await flush();
  assert.equal(badgeUpdates.at(-1), 1, "Reading one matter must not clear another unread matter");
  context.window.PilotPush.update([]); await flush();
  assert.equal(badgeUpdates.at(-1), 0);
  // A slow window badge operation must finish before the newer read receipt.
  let finishBadge;
  context.navigator.setAppBadge = count => new Promise(resolve => {
    finishBadge = () => { badgeUpdates.push(count); resolve(); };
  });
  context.window.PilotPush.update([{id:'slow', noticeID:'one', unread:true}]); await flush();
  context.window.PilotPush.update([]);
  finishBadge(); await flush();
  assert.equal(badgeUpdates.at(-1), 0, 'Slow page badge writes must not overtake an acknowledgment');
  assert.equal(stateMessages.at(-1).notices.length, 0);
  context.navigator.setAppBadge = async () => { throw new Error('badge denied'); };
  context.window.PilotPush.update([{id:'denied', noticeID:'one', unread:true}]); await flush();
  assert.equal(stateMessages.at(-1).notices[0].session, 'denied', 'Window badge failure cannot block worker reconciliation');
  context.window.PilotPush.update([]); await flush();
  delete context.navigator.clearAppBadge;
  context.window.PilotPush.update([]); await flush();
  assert.equal(stateMessages.at(-1).notices.length, 0, "Worker reconciliation also supports clients without window badge APIs");
  await node("push-disable").handlers.click();
  assert.equal(sub, null); assert.equal(node("push-enable").hidden, false);

  // Replacing the controller before initial state arrives must poll without
  // sending an invented empty snapshot or throwing the old signature error.
  badgeUpdates.length = 0; stateMessages.length = 0;
  let resumedPolls = 0;
  context.poll = () => { resumedPolls++; };
  vm.runInNewContext(source('pwa.js'), context); await flush();
  serviceEvents.controllerchange(); await flush();
  assert.equal(resumedPolls, 1);
  assert.equal(badgeUpdates.length, 0);
  assert.equal(stateMessages.length, 0);
  console.log("Push UI checks passed: visible pushes, sound request, badge reconciliation, notification routing, Safari gesture permission, subscription and test/disable controls.");
})().catch(error => {console.error(error); process.exitCode = 1;});
