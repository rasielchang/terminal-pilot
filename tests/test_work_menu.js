'use strict';
const assert = require('node:assert/strict');
const {browser, state, flush} = require('./test_assistant');
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
  // Document scrolling on phones: the fixed bar must not cover the reply,
  // the bottom approval card, or the source reader header.
  const phone = browser({workMenu:true});
  phone.context.window.innerWidth = 390;
  phone.scrollingElement.clientHeight = 844;
  phone.scrollingElement.scrollHeight = 16000;
  const reply = {id:1, role:'assistant', kind:'result', created:1, text:'Long result\n'.repeat(200)};
  const job = {id:'job-a', session:'one', title:'Source', state:'completed', label:'Done'};
  phone.assistant.update();
  phone.calls.at(-1).resolve(state({messages:[reply], jobs:[job]})); await flush();
  const message = phone.$('brain-messages').children[0]; message.layoutTop = 8000;
  phone.frame();
  assert.equal(message.getBoundingClientRect().top, 92);
  assert.equal(phone.$('assistant-home').style.overflow, 'visible', 'Phone scroll belongs to the document');
  phone.$('mode-bar').layoutHeight = 112; // Wrapped labels, rotation or enlarged text.
  phone.$('brain-latest').click(); phone.frame();
  assert.equal(message.getBoundingClientRect().top, 124);
  phone.scrollingElement.scrollTop = 700;
  phone.assistant.update(); phone.calls.at(-1).resolve(state({messages:[reply], jobs:[job]}));
  await flush(); phone.frame();
  assert.equal(phone.scrollingElement.scrollTop, 700, 'Polling must preserve reading position');
  phone.$('brain-source').layoutTop = 300;
  phone.$('brain-jobs').children[0].children.at(-1).children[0].click();
  assert.equal(phone.$('brain-source').getBoundingClientRect().top, 124);
  phone.calls.at(-1).resolve({available:true, instruction:'Read', result:'Result', messages:[]}); await flush();
  phone.$('brain-source-close').click(); phone.frame();
  assert.equal(phone.scrollingElement.scrollTop, 700, 'Closing a source returns to the exact reading position');
  phone.assistant.update();
  phone.calls.at(-1).resolve(state({messages:[reply], decisions:[{
    session:{id:'one', binding:'exact', title:'Decision'}, requests:[{id:'approval', state:'pending'}]
  }]})); await flush();
  phone.$('brain-decisions').layoutTop = 12000;
  phone.$('brain-latest').click(); phone.frame();
  assert.equal(phone.$('brain-decisions').getBoundingClientRect().top, 124);
  phone.$('brain-latest').click(); phone.scrollingElement.scrollTop = 500; phone.frame();
  assert.equal(phone.scrollingElement.scrollTop, 500, 'User scrolling wins over queued navigation');
  phone.context.mobileLayout.matches = false; phone.context.window.innerWidth = 820;
  phone.assistant.refresh();
  assert.equal(phone.scrollingElement.scrollTop, 0, 'Crossing the breakpoint must release the former document scroll');
  assert.equal(phone.$('assistant-home').scrollTop, 500);
  phone.$('assistant-home').scrollTop = 650;
  phone.context.mobileLayout.matches = true; phone.context.window.innerWidth = 390;
  phone.assistant.refresh();
  assert.equal(phone.$('assistant-home').scrollTop, 0);
  assert.equal(phone.scrollingElement.scrollTop, 650, 'Returning to the phone transfers the current reading position');

  const desktop = browser({workMenu:true});
  desktop.context.mobileLayout.matches = false; desktop.context.window.innerWidth = 1400;
  desktop.$('assistant-home').layoutTop = 120;
  desktop.assistant.update(); desktop.calls.at(-1).resolve(state({messages:[reply]})); await flush(); desktop.frame();
  assert.equal(desktop.$('assistant-home').scrollTop, 3000 - 120 - 12,
    'A desktop scroller already below the bar must not receive the phone offset twice');
  console.log('Work menu: exact work navigation, draft preservation, stable polling, counts and logout cleanup passed.');
})().catch(error => {console.error(error); process.exitCode=1;});
