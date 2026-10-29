"use strict";
const assert = require("node:assert/strict");
const {browser, flush} = require("./test_navigation.js");

function attention(saved) {
  const app = browser(saved);
  app.run(`
    token = "test"; restoreRoute = false;
    var remote = [
      {id: "a", tool: "claude", unread: true, noticeID: "a1", phase: "complete", status: "回覆完成 · 待查看"},
      {id: "b", unread: true, noticeID: "b1", phase: "complete"}
    ];
    sessions = remote.map(session => ({...session}));
    var ackCalls = [];
    api = async (path, data) => {
      if (path === "/api/state") return {sessions: remote.map(session => ({...session}))};
      if (path.startsWith("/api/thread?")) {
        const id = new URLSearchParams(path.split("?")[1]).get("session");
        return {session: {...remote.find(session => session.id === id)}, messages: [], source: "none"};
      }
      if (path === "/api/ack") {
        ackCalls.push({...data});
        const session = remote.find(session => session.id === data.session);
        if (session.noticeID !== data.noticeID) throw new Error("有新的待處理通知");
        session.unread = false;
        return {acknowledged: true};
      }
      throw new Error("Unexpected endpoint " + path);
    };
  `);
  return app;
}

(async () => {
  const app = attention();
  await app.run('selectSession("a")');
  assert.equal(app.run('ackCalls.length'), 1);
  assert.equal(app.run('ackCalls[0].noticeID'), "a1");
  assert.equal(app.run('sessions.find(s => s.id === "a").unread'), false);
  assert.equal(app.run('sessions.find(s => s.id === "b").unread'), true);
  // An older in-flight state/thread response cannot relight a confirmed notice.
  await app.run('remote[0].unread = true; loadThread = originalLoadThread; poll();');
  assert.equal(app.run('current().unread'), false);
  // Remaining in the conversation and polling a new completion must keep it unread.
  await app.run('remote[0].noticeID = "a2"; remote[0].unread = true; poll();');
  assert.equal(app.run('current().unread'), true);
  assert.equal(app.run('ackCalls.length'), 1);
  app.history.back(); await flush();
  await app.run('poll()');
  assert.equal(app.run('ackCalls.length'), 1);
  app.history.forward();
  for (let i = 0; i < 4; i++) await flush();
  assert.equal(app.run('ackCalls.length'), 2);
  assert.equal(app.run('ackCalls[1].noticeID'), "a2");

  const reloaded = attention(structuredClone(app.navigation));
  await reloaded.run('restoreRoute = true; poll();');
  assert.equal(reloaded.run('selected'), "a");
  assert.equal(reloaded.run('ackCalls.length'), 0, "Reload must not acknowledge newer notifications");
  const desktop = attention();
  await desktop.run('mobileLayout.matches = false; poll();');
  assert.equal(desktop.run('selected'), "a");
  assert.equal(desktop.run('ackCalls.length'), 0, "Automatic desktop selection must not mark read");

  const race = attention();
  await race.run(`loadThread = async () => {
    remote[0].noticeID = "a-new"; sessions[0] = {...remote[0]}; return true;
  }; selectSession("a");`);
  assert.equal(race.run('ackCalls[0].noticeID'), "a1", "Entry must capture the clicked notification");
  assert.equal(race.run('current().unread'), true);
  assert.equal(race.run('acknowledgedNotices.size'), 0);

  const failed = attention();
  await failed.run('loadThread = async () => false; selectSession("a");');
  assert.equal(failed.run('ackCalls.length'), 0);
  assert.equal(failed.run('current().unread'), true);
  const left = attention();
  const pending = left.run('var finishRead; loadThread = () => new Promise(resolve => finishRead = resolve); selectSession("a");');
  left.run('showSessionList(); finishRead(true);');
  await pending;
  assert.equal(left.run('ackCalls.length'), 0, "Leaving before loading must not acknowledge");
  const hidden = attention();
  hidden.document.hidden = true;
  await hidden.run('selectSession("a")');
  assert.equal(hidden.run('ackCalls.length'), 0);
  const alreadyRead = attention();
  await alreadyRead.run(`sessions[0].unread = false; loadThread = async () => {
    remote[0].noticeID = "later"; sessions[0] = {...remote[0]}; return true;
  }; selectSession("a");`);
  assert.equal(alreadyRead.run('ackCalls.length'), 0, "Opening a read row must not clear a later completion");
  const badge = attention();
  await badge.run(`
    var pushedStates = [];
    window.PilotPush = {update: items => pushedStates.push(items.map(s => ({...s})))};
    notificationSessions = remote.map(s => ({...s}));
    var afterAckPoll;
    poll = () => new Promise(resolve => afterAckPoll = resolve);
  `);
  const ack = badge.run('acknowledgeNotice("a", "a1")');
  await flush();
  assert.equal(badge.run('pushedStates.at(-1).find(s => s.id === "a").unread'), false,
    'A confirmed ack must clear the icon immediately, without waiting for another HTTP response');
  badge.run('afterAckPoll()'); await ack;
  const staleBadge = attention();
  await staleBadge.run(`
    var pushedStates = [];
    acknowledgedNotices.set("a", "a1");
    window.PilotPush = {update: items => pushedStates.push(items.map(s => ({...s})))};
    api = async () => ({sessions: remote, notifications: [...remote, {id:"brain", unread:true, noticeID:"result"}]});
    poll();
  `);
  assert.equal(staleBadge.run('pushedStates.at(-1)[0].unread'), false, 'Stale notification snapshots relit an acknowledged work');
  assert.equal(staleBadge.run('pushedStates.at(-1).at(-1).unread'), true, 'Work ack must preserve distinct Brain results');
  const resumed = attention();
  resumed.run(`var refreshes = 0, updates = []; window.PilotPush = {
    invalidate() { refreshes++; }, update(items) { updates.push(items.map(s => ({...s}))); }
  }; remote[0].unread = false;`);
  resumed.document.hidden = true;
  resumed.events.get('visibilitychange')();
  assert.equal(resumed.run('refreshes'), 0);
  resumed.document.hidden = false;
  for (const event of ['visibilitychange', 'pageshow', 'online']) {
    resumed.events.get(event)(); await flush();
    assert.equal(resumed.run('updates.at(-1)[0].unread'), false, 'Resume must fetch shared authoritative read state');
    assert.equal(resumed.run('updates.at(-1)[1].unread'), true);
  }
  assert.equal(resumed.run('refreshes'), 3);
  assert.equal(resumed.run('ackCalls.length'), 0, 'Foreground events cannot mark unseen work read');
  console.log("Attention checks passed: explicit entry, shared read state, newer-notice races, polling, history, reload, failed loads and background pages.");
})().catch(error => { console.error(error); process.exitCode = 1; });
