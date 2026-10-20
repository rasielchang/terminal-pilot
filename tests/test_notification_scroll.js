"use strict";
const assert = require("node:assert/strict");
const {browser, flush} = require("./test_navigation.js");

function page(options) {
  const app = browser(undefined, options);
  let top = 0;
  const scroll = app.document.scrollingElement;
  Object.defineProperty(scroll, "scrollTop", {
    get: () => top,
    set: value => { top = Math.max(0, Math.min(value, scroll.scrollHeight - scroll.clientHeight)); }
  });
  app.run(`
    token = "paired"; restoreRoute = false; loadThread = originalLoadThread;
    var remote = [{id: "a", tool: "claude"}, {id: "b", tool: "codex"}];
    sessions = remote;
    var failThread = false;
    var transcript = [];
    api = async path => {
      if (path === "/api/state") return {sessions: remote};
      if (failThread) throw new Error("offline");
      const id = new URLSearchParams(path.split("?")[1]).get("session");
      return {session: remote.find(s => s.id === id), messages: transcript, source: "codex"};
    };
  `);
  return app;
}
function paint(app, height = 12000) {
  app.frame();
  // On iPhone the revealed document's height may settle after the first frame.
  app.document.scrollingElement.scrollHeight = height;
  app.frame();
}
function atLatest(app) {
  const scroll = app.document.scrollingElement;
  assert.equal(scroll.scrollTop, scroll.scrollHeight - scroll.clientHeight);
}

(async () => {
  const cold = page({search: "?work=a"});
  await cold.run("poll()");
  paint(cold); atLatest(cold);
  assert.equal(cold.run("selected"), "a");

  // Warm PWA, already loaded conversation, no message diff at all.
  const warm = page();
  await warm.run('selectSession("a")');
  warm.document.scrollingElement.scrollTop = 100;
  warm.run("showSessionList()");
  warm.run('openPushWork("a")'); await flush();
  paint(warm); atLatest(warm);
  assert.equal(warm.history.state.pilot.view, "thread");

  // Notification while that conversation is already visible must also jump.
  warm.document.scrollingElement.scrollTop = 200;
  warm.run('openPushWork("a")'); await flush();
  paint(warm, 18000); atLatest(warm);
  // Once positioned, ordinary polls and returning from the list preserve reading.
  warm.document.scrollingElement.scrollTop = 700;
  await warm.run("poll()"); paint(warm, 18000);
  assert.equal(warm.document.scrollingElement.scrollTop, 700);
  warm.run("showSessionList()");
  await warm.run('selectSession("a")'); paint(warm, 18000);
  assert.equal(warm.document.scrollingElement.scrollTop, 700);

  // A notification received while another conversation is selected targets its own page.
  const another = page();
  await another.run('selectSession("b")');
  another.run('openPushWork("a")'); await flush();
  paint(another); atLatest(another);
  assert.equal(another.run("selected"), "a");

  // Background layout and failed loads do not consume the requested position.
  const hidden = page({search: "?work=a"});
  hidden.document.hidden = true;
  await hidden.run("poll()");
  hidden.document.scrollingElement.scrollTop = 0;
  paint(hidden);
  assert.equal(hidden.document.scrollingElement.scrollTop, 0);
  hidden.document.hidden = false;
  await hidden.run("poll()"); paint(hidden); atLatest(hidden);
  const failed = page({search: "?work=a"});
  await failed.run("failThread = true; poll()"); paint(failed);
  assert.equal(failed.document.scrollingElement.scrollTop, 0);
  await failed.run("failThread = false; poll()"); paint(failed); atLatest(failed);

  // Scheduled callbacks cannot move the list or another conversation after navigation.
  const left = page({search: "?work=a"});
  await left.run("poll()");
  left.run("showSessionList()");
  left.document.scrollingElement.scrollTop = 350;
  paint(left);
  assert.equal(left.document.scrollingElement.scrollTop, 350);
  const switched = page({search: "?work=a"});
  await switched.run("poll()");
  await switched.run('selectSession("b")');
  switched.document.scrollingElement.scrollTop = 250;
  paint(switched);
  assert.equal(switched.document.scrollingElement.scrollTop, 250);

  const long = page();
  long.run(`transcript = [{id:'u',role:'user',text:'問題'}, {id:'r',role:'assistant',text:'長回覆\\n'.repeat(200)}]`);
  await long.run('selectSession("a")');
  long.node('messages').children.at(-1).layoutTop = 8000;
  paint(long);
  assert.equal(long.document.scrollingElement.scrollTop, 7888, 'Open at the reply heading below the sticky header, not its end');
  long.document.scrollingElement.scrollTop = 9000;
  await long.run('loadThread()'); paint(long);
  assert.equal(long.document.scrollingElement.scrollTop, 9000, 'Unchanged polls preserve a long reply reading position');
  long.run('showSessionList()'); await long.run('selectSession("a")'); paint(long);
  assert.equal(long.document.scrollingElement.scrollTop, 9000, 'Return from the list keeps the reading position');
  long.run('openPushWork("a")'); await flush(); paint(long);
  assert.equal(long.document.scrollingElement.scrollTop, 7888, 'A new notification reopens the same reply at its beginning');
  long.document.scrollingElement.scrollTop = 11350;
  await long.run('transcript[1].text += "新增內容"; loadThread()');
  paint(long, 18000);
  assert.equal(long.document.scrollingElement.scrollTop, 11350, 'Growing the same reply never scrolls the reader');
  long.document.scrollingElement.scrollTop = 17400;
  await long.run('transcript.push({id:"r2",role:"assistant",text:"新回覆"}); loadThread()');
  long.node('messages').children.at(-1).layoutTop = 17900;
  paint(long, 24000);
  assert.equal(long.document.scrollingElement.scrollTop, 17788, 'Follow a newly arrived reply from its beginning');
  long.document.scrollingElement.scrollTop = 500;
  await long.run('transcript.push({id:"r3",role:"assistant",text:"另一則"}); loadThread()');
  paint(long, 26000);
  assert.equal(long.document.scrollingElement.scrollTop, 500, 'Do not pull readers away from earlier messages');
  console.log("Notification scroll checks passed: cold/warm entry, unchanged content, delayed layout, foreground retry, failed loads, normal reading and navigation races.");
})().catch(error => { console.error(error); process.exitCode = 1; });
