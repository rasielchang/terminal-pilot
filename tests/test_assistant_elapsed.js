'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {browser, state, flush} = require('./test_assistant');
(async () => {
  const b = browser(), timers = [];
  b.context.window.setInterval = (fn, interval) => { assert.equal(interval, 1000); timers.push(fn); };
  vm.runInContext('Date.now = () => 2000000', b.context);
  vm.runInContext(fs.readFileSync('web/assistant.js', 'utf8'), b.context);
  const a = b.context.window.PilotAssistant;
  const pending = state({working:true, backend:{detail:'正在讀取文件'}, messages:[
    {id:1,role:'assistant',kind:'result',created:1800,text:'上一輪'},
    {id:2,role:'user',kind:'reply',created:1875,text:'目前交代'}, {id:3,role:'assistant',kind:'question',created:1900,text:'approval prompt'}]});
  a.update(); b.calls.at(-1).resolve(pending); await flush();
  assert.match(b.$('brain-status').textContent, /2 分 05 秒/);
  b.$('brain-input').value = '保留草稿';
  vm.runInContext('Date.now = () => 2001000', b.context);
  timers[0]();
  assert.match(b.$('brain-status').textContent, /2 分 06 秒/);
  assert.equal(b.$('brain-input').value, '保留草稿');
  const clockText = b.$('brain-status').textContent;
  const reloaded = browser();
  vm.runInContext('Date.now = () => 2001000', reloaded.context);
  reloaded.assistant.update(); reloaded.calls.at(-1).resolve(pending); await flush();
  assert.equal(reloaded.$('brain-status').textContent, clockText, 'Reload uses durable message timestamp');
  a.update(); b.calls.at(-1).resolve({...pending,decisions:[{session:{id:'one'},requests:[]}]}); await flush();
  assert.match(b.$('brain-status').textContent, /等待你在輸入框前確認/);
  b.context.connected = false; timers[0]();
  assert.match(b.$('brain-status').textContent, /無法確認進度/);
  assert.doesNotMatch(b.$('brain-status').textContent, /正在讀取/);
  b.context.connected = true;
  a.update(); b.calls.at(-1).resolve(state()); await flush();
  assert.doesNotMatch(b.$('brain-status').textContent, /已經過/);
  a.update(); b.calls.at(-1).resolve(pending); await flush();
  vm.runInContext('Date.now = () => 2020000', b.context); timers[0]();
  assert.match(b.$('brain-status').textContent, /無法確認進度/, 'Stale status cannot claim active execution');
  console.log('Assistant elapsed checks passed: ticking, durable reload, drafts, approval, disconnect, stale status and idle.');
})().catch(error => {console.error(error); process.exitCode=1;});
