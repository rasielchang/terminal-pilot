"use strict";
const assert = require("node:assert/strict");
const {browser} = require("./test_navigation.js");
function sender() {
  const app = browser();
  app.run(`token = 'paired'; connected = true; selected = 'a';
    sessions = [{id:'a', binding:'tty:a', tool:'codex', canSend:true}, {id:'b', binding:'tty:b', tool:'claude', canSend:true}];
    poll = () => {}; var calls = []; var response = {sent:true};
    api = async (path, data) => { calls.push({path, data}); return response; };
    drafts.set('a', '原草稿'); $('message').value = '原草稿';`);
  return app;
}
(async () => {
  const voice = sender();
  await voice.run("sendAgentReply({session:'a',binding:'tty:a'}, '語音指令', 'voice-1')");
  assert.equal(voice.run("drafts.get('a')"), "原草稿"); assert.equal(voice.node("message").value, "原草稿");
  assert.equal(voice.run("calls[0].data.requestID"), "voice-1");
  assert.equal(voice.run("feedback.get('a').sentText"), "語音指令");
  await voice.run("sendAgentReply({session:'a',binding:'tty:a'}, '原草稿', 'text-1', {clearDraft:true})");
  assert.equal(voice.run("drafts.get('a')"), ""); assert.equal(voice.node("message").value, "");

  const concurrent = sender();
  const pending = concurrent.run("var finish; api = (path, data) => { calls.push({path,data}); return new Promise(resolve => finish = resolve); }; sendAgentReply({session:'a',binding:'tty:a'}, '第一筆', 'first')");
  await assert.rejects(concurrent.run("sendAgentReply({session:'a',binding:'tty:a'}, '第二筆', 'second')"), /正在傳送/);
  assert.equal(concurrent.run("calls.length"), 1);
  concurrent.run("selected = 'b'; $('message').value = '另一個工作草稿'; finish({sent:true})"); await pending;
  assert.equal(concurrent.node("message").value, "另一個工作草稿");
  assert.equal(concurrent.run("feedback.get('a').sentText"), "第一筆");
  assert.equal(concurrent.run("sending.size"), 0);

  for (const setup of ["connected = false", "sessions[0].canSend = false", "sessions[0].binding = 'new-tty'", "selected = 'b'"]) {
    const invalid = sender(); invalid.run(setup);
    await assert.rejects(invalid.run("sendAgentReply({session:'a',binding:'tty:a'}, '不要送出', 'bad')"));
    assert.equal(invalid.run("calls.length"), 0);
  }
  const uncertain = sender(); uncertain.run("response = {sent:false, uncertain:true}");
  await assert.rejects(uncertain.run("sendAgentReply({session:'a',binding:'tty:a'}, '原草稿', 'uncertain', {clearDraft:true})"), /無法確認/);
  assert.equal(uncertain.run("calls.length"), 1); assert.equal(uncertain.run("drafts.get('a')"), "原草稿");
  assert.equal(uncertain.run("feedback.get('a').error"), true); assert.equal(uncertain.run("sending.size"), 0);
  console.log("Shared send checks passed: text and voice use the same target checks, delivery lock, request IDs, feedback and draft preservation.");
})().catch(error => { console.error(error); process.exitCode = 1; });
