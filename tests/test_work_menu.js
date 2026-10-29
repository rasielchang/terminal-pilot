'use strict';
const assert = require('node:assert/strict');
const {browser} = require('./test_assistant');
(async () => {
  const b = browser({workMenu:true});
  b.context.sessions = [
    {id:'one', title:'Active work', phase:'running'},
    {id:'two', title:'Decision', phase:'waiting', turnProgress:{state:'active'}},
    {id:'three', title:'Saved', phase:'idle'}
  ];
  const menu = b.$('workspace').children.find(n => n.id === 'work-menu');
  const trigger = b.$('mode-bar').children.find(n => n.id === 'work-menu-open');
  const rows = menu.children.find(n => n.id === 'work-menu-sessions');
  b.assistant.refresh();
  assert.match(trigger.textContent, /1 執行/);
  assert.match(trigger.textContent, /1 待決定/);
  assert.equal(rows.children[0].dataset.session, 'two');
  const first = rows.children[0];
  b.context.sessions[0].updatedAt = 100;
  b.assistant.refresh();
  assert.equal(rows.children[0], first, 'Polling must retain focused menu rows when only timestamps change');
  b.$('brain-input').value = 'Unsent assistant draft';
  trigger.click(); assert.equal(menu.open, true);
  await rows.children[0].click();
  assert.equal(menu.open, false);
  assert.equal(b.opened.at(-1), 'two');
  assert.equal(b.assistant.mode(), 'manager');
  assert.equal(b.$('mode-bar').style.position, 'static');
  b.assistant.chooseMode('assistant');
  assert.equal(b.$('brain-input').value, 'Unsent assistant draft');
  assert.equal(b.$('mode-bar').style.position, 'fixed');
  trigger.click(); b.assistant.reset();
  assert.equal(menu.open, false);
  b.context.sessions=[]; b.context.connected=false; b.assistant.refresh();
  assert.match(trigger.textContent, /離線/);
  assert.equal(rows.children.some(n => n.dataset.session), false);
  console.log('Work menu: exact work navigation, draft preservation, stable polling, counts and logout cleanup passed.');
})().catch(error => {console.error(error); process.exitCode=1;});
