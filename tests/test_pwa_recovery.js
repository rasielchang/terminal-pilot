"use strict";
const assert = require("node:assert/strict");
const {browser, flush} = require("./test_navigation.js");
const brain = require("./test_assistant.js");

function pilot() {
  const app = browser();
  app.node("send").firstChild = {textContent: ""};
  app.run(`connection = originalConnection;
    globalThis.navigator = {onLine: true};
    var calls = [], clock = 1000;
    Date.now = () => clock;
    globalThis.fetch = (path, options) => new Promise(resolve => calls.push({
      path, options, reply(body, status = 200) {
        resolve({status, ok: status === 200, json: async () => body});
      }
    }));
    var fixture = {id:"a", title:"最後有效工作", binding:"tty:a", canSend:true, tool:"codex"};
    token = "fixture"; sessions = [fixture]; selected = "a"; connection(true);`);
  return app;
}

(async () => {
  const app = pilot();
  const old = app.run("poll()");
  app.document.hidden = true;
  app.events.get("visibilitychange")();
  app.document.hidden = false;
  app.events.get("visibilitychange")();
  app.events.get("pageshow")({persisted: true});
  app.run("clock += 500");
  app.events.get("online")();
  assert.equal(app.run("calls.length"), 2);
  assert.equal(app.run("calls[0].options.signal.aborted"), true);
  assert.equal(app.run("sessions[0].title"), "最後有效工作");
  assert.equal(app.node("connection-recovery").hidden, false);
  app.run('calls[1].reply({sessions:[{...fixture, title:"新狀態"}]})');
  await flush();
  assert.equal(app.run("sessions[0].title"), "新狀態");
  assert.equal(app.node("connection-recovery").hidden, true);
  app.run('calls[0].reply({error:"old unauthorized"}, 401)');
  await old;
  assert.equal(app.run("token"), "fixture", "A retired read cannot log out the resumed app");
  assert.equal(app.run("sessions[0].title"), "新狀態");

  const thread = pilot();
  await thread.run('restoreRoute = false; $("workspace").classList.add("detail"); loadThread = originalLoadThread;');
  const oldThread = thread.run("loadThread()");
  thread.events.get("pageshow")({persisted: true});
  assert.equal(thread.run("calls[0].options.signal.aborted"), true);
  thread.run("calls[1].reply({sessions:[fixture]})");
  await flush();
  assert.equal(thread.run("calls[2].path"), "/api/thread?session=a");
  thread.run('calls[2].reply({session:fixture, messages:[{id:2, role:"assistant", text:"新內容"}], source:"codex"})');
  await flush();
  const newest = thread.node("messages").children[0];
  assert.equal(newest?.dataset.messageId, "2");
  thread.run('calls[0].reply({session:fixture, messages:[{id:1, role:"assistant", text:"過時內容"}], source:"codex"})');
  await oldThread;
  assert.equal(thread.node("messages").children[0], newest);

  const viewed = pilot();
  const selection = viewed.run(`loadThread = originalLoadThread; restoreRoute = false;
    fixture.unread = true; fixture.noticeID = "selected-notice";
    selectSession("a")`);
  viewed.events.get("pageshow")({persisted: true});
  viewed.run("calls[1].reply({sessions:[fixture]})");
  await flush();
  viewed.run('calls[2].reply({session:fixture, messages:[], source:"codex"})');
  await flush();
  assert.equal(viewed.run("calls[3].path"), "/api/ack");
  assert.equal(viewed.run("JSON.parse(calls[3].options.body).noticeID"), "selected-notice");
  viewed.run('calls[0].reply({session:fixture, messages:[], source:"codex"}); calls[3].reply({acknowledged:true})');
  await selection;
  await flush();
  assert.equal(viewed.run('calls.filter(call => call.path === "/api/ack").length'), 1);

  const offline = pilot();
  offline.run('navigator.onLine = false; sessions = []; selected = ""; showWorkspace()');
  offline.events.get("pageshow")({persisted: false});
  assert.equal(offline.run("calls.length"), 0, "Offline boot skips network reads");
  assert.equal(offline.node("workspace").hidden, false);
  assert.equal(offline.node("connection-recovery").hidden, false);
  offline.run("navigator.onLine = true");
  offline.events.get("online")();
  assert.equal(offline.run("calls.length"), 1);
  offline.run("calls[0].reply({sessions:[fixture]})");
  await flush();
  assert.equal(offline.node("connection-recovery").hidden, true);

  const sending = pilot();
  sending.node("message").value = "保留的草稿";
  sending.run('drafts.set("a", $("message").value);');
  const send = sending.run('sendAgentReply({session:"a",binding:"tty:a"}, "保留的草稿", "single-send", {clearDraft:true})');
  const state = sending.run("poll()");
  sending.run("navigator.onLine = false");
  sending.events.get("offline")();
  assert.equal(sending.run("calls[0].options.signal.aborted"), false, "The POST remains in flight");
  assert.equal(sending.run("calls[1].options.signal.aborted"), true);
  sending.run("navigator.onLine = true");
  sending.events.get("online")();
  sending.events.get("pageshow")({persisted: true});
  assert.equal(sending.run("calls.length"), 3);
  sending.run('calls[1].reply({sessions:[]}); calls[2].reply({sessions:[fixture]});');
  await state;
  await flush();
  sending.run("calls[0].reply({sent:true})");
  await send;
  assert.equal(sending.run('calls.filter(call => call.options.method === "POST").length'), 1);
  assert.equal(sending.node("message").value, "");
  sending.run("calls[3].reply({sessions:[fixture]})");
  await flush();

  const assistant = brain.browser();
  const message = (id, text) => ({id, text, role:"assistant", kind:"reply", created:id});
  assistant.assistant.update();
  assistant.calls[0].resolve(brain.state({messages:[message(1, "既有對話")]}));
  await brain.flush();
  assistant.assistant.update();
  assistant.assistant.invalidateRead();
  assistant.context.connected = false;
  assistant.assistant.refresh();
  assert.equal(assistant.$("brain-messages").children[0].dataset.messageId, "1");
  assert.equal(assistant.calls[1].options.signal.aborted, true);
  assistant.context.connected = true;
  assistant.assistant.update();
  assistant.assistant.update();
  assert.equal(assistant.calls.length, 3);
  assistant.calls[2].resolve(brain.state({messages:[message(1, "既有對話"), message(3, "恢復後對話")]}));
  await brain.flush();
  assistant.calls[1].resolve(brain.state({messages:[message(2, "過時對話")]}));
  await brain.flush();
  assert.deepEqual(assistant.$("brain-messages").children.map(node => node.dataset.messageId), ["1", "3"]);

  console.log("PWA recovery checks passed: hung state, thread and Brain reads; coalesced resume; late results and 401; offline boot; single send.");
})().catch(error => { console.error(error); process.exitCode = 1; });
