'use strict';
const assert = require('node:assert/strict');
const {browser, flush} = require('./test_navigation');
(async () => {
  const deepLink = browser(undefined, {hash: '#view=assistant&work=a'});
  assert.equal(deepLink.run('incomingWork'), 'a');
  const restored = browser({entries: [{pilot: {view: 'thread', session: 'a', mode: 'assistant'}}], index: 0}, {hash: '#view=assistant&work=a'});
  assert.equal(restored.run('incomingWork'), '', 'Reload must not acknowledge a newer notification');
  const app = browser();
  app.run(`let presentation = 'assistant'; window.PilotAssistant = {mode: () => presentation, restore: route => { presentation = route.mode || presentation; }, refresh() {}}; replaceListRoute();`);
  await app.run('selectSession("a")');
  assert.equal(app.history.state.pilot.mode, 'assistant');
  app.node('message').value = '尚未送出的草稿';
  app.history.back(); await flush();
  assert.equal(app.run('presentation'), 'assistant');
  assert.equal(app.run('drafts.get("a")'), '尚未送出的草稿');
  app.history.forward(); await flush();
  assert.equal(app.node('message').value, '尚未送出的草稿');
  // Changing modes keeps the draft and records the return destination, instead
  // of silently opening another work or dropping the mode on history restore.
  app.run(`showSessionList(); presentation = 'manager'; replaceListRoute();`);
  await app.run('selectSession("b")');
  assert.equal(app.history.state.pilot.mode, 'manager');
  app.history.back(); await flush();
  assert.equal(app.run('presentation'), 'manager');
  assert.equal(app.run('drafts.get("a")'), '尚未送出的草稿');
  console.log('Assistant navigation checks passed: mode-aware back/forward, exact conversation and drafts retained across modes.');
})().catch(error => {console.error(error); process.exitCode=1;});
