'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
function browser({workMenu = false} = {}) {
  const nodes = new Map(), calls = [], opened = [], frames = [], preferences = new Map();
  let serial = 0;
  function node(tag = '') {
    const classes = new Set();
    return {tag, value: '', textContent: '', children: [], dataset: {}, style: {}, hidden: false, handlers: {}, attrs: {}, scrollHeight: 3000, scrollTop: 0, clientHeight: 600, clientTop: 0, layoutTop: 3000,
      getBoundingClientRect() { const height = this.layoutHeight || 80; const top = this.style.position === 'fixed' ? 0 : this.layoutTop - scrollingElement.scrollTop; return {top, bottom: top + height, height}; },
      classList: {toggle(name, value) { value ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name)},
      setAttribute(key, value) { this.attrs[key] = value; },
      append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
      addEventListener(name, fn) { this.handlers[name] = fn; }, click() { return this.handlers.click?.(); }, scrollIntoView() {},
      showModal() { this.open = true; }, close() { this.open = false; }, focus() { this.focused = true; }};
  }
  const $ = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  $('mode-bar').querySelector = () => $('mode-brand');
  const scrollingElement = node();
  const context = vm.createContext({$, sessions: [{id:'one',binding:'tty1'}], selected: 'one', token: 'paired', connected: true, listScrollTop: 0,
    mobileLayout: {matches: true}, requestID: () => `request-${++serial}`,
    history: {state: {pilot: {view: 'list'}}, replaceState(s) { this.state=s; }, pushState(s) { this.state=s; }}, fragment: new URLSearchParams(''), location:{pathname:'/'},
    localStorage: {getItem: key => preferences.get(key), setItem: (k, v) => preferences.set(k, v)},
    document: {hidden: false, scrollingElement, querySelector: selector => workMenu && selector === '.mode-bar' ? $('mode-bar') : null, createTextNode: text => ({textContent: text})},
    window: {scrollTo() {}, addEventListener() {}, PilotLaunch: {cancelNavigation() {}},
      PilotApprovals: {renderCard: (s,r,o) => ({session:s, request:r, options:o})}},
    requestAnimationFrame(fn) { frames.push(fn); },
    element(tag, cls, text) { return Object.assign(node(tag), {className: cls || '', textContent: text || ''}); },
    renderText: text => ({textContent: text}),
    current: () => context.sessions.find(s => s.id === context.selected),
    threadVisible: () => false, replaceListRoute() {}, recordThreadRoute() {}, showSessionList() {},
    selectSession: async id => { context.selected = id; opened.push(id); },
    api: (path, data) => new Promise((resolve, reject) => calls.push({path, data, resolve, reject}))});
  vm.runInContext(fs.readFileSync('web/reading.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('web/assistant.js', 'utf8'), context);
  return {context, $, calls, opened, preferences, assistant: context.window.PilotAssistant, scrollingElement,
    frame(count = 2) { while (count-- > 0) frames.splice(0).forEach(fn => fn()); }};
}
const state = (extra={}) => ({messages:[],jobs:[],decisions:[],paused:false,working:false,memory:{available:true,path:'/fixture/brain'},...extra});
const flush = async () => { for (let i=0; i<12; i++) await Promise.resolve(); };
if (require.main === module) (async () => {
  const b = browser(), a = b.assistant;
  b.$('message').value = '管理畫面的草稿';
  a.update(); assert.equal(b.calls.length, 1); assert.equal(b.calls[0].path, '/api/brain'); assert.equal(b.calls[0].data, undefined);
  b.calls[0].resolve(state()); await flush();
  b.frame(); assert.equal(b.scrollingElement.scrollTop, 0, 'An empty welcome keeps the mode switch and introduction visible');
  assert.equal(b.$('brain-send').disabled, true);
  b.$('brain-input').value = '請查網站'; b.$('brain-input').handlers.input(); assert.equal(b.$('brain-send').disabled, false);
  a.chooseMode('manager'); assert.equal(b.$('assistant-home').hidden, true); assert.equal(b.$('messages').hidden, false);
  assert.equal(b.$('message').value, '管理畫面的草稿'); assert.equal(b.$('brain-input').value, '請查網站');
  a.chooseMode('assistant'); b.calls.at(-1).resolve(state()); await flush();
  b.$('brain-compose').handlers.submit({preventDefault(){}});
  const first = b.calls.at(-1); assert.equal(first.path, '/api/brain/message'); assert.equal(first.data.text, '請查網站');
  b.$('brain-compose').handlers.submit({preventDefault(){}}); assert.equal(b.calls.at(-1), first, 'Double tap is one request');
  first.reject(new TypeError('load failed')); await flush();
  assert.match(b.$('brain-error').textContent, /尚未確認/); assert.equal(b.$('brain-input').value, '請查網站');
  b.$('brain-input').value = '送出期間的新草稿';
  b.$('brain-compose').handlers.submit({preventDefault(){}});
  const retry = b.calls.at(-1); assert.deepEqual(retry.data, first.data, 'Retry uses identical durable ID and text');
  retry.resolve({accepted:true,messageID:1}); await flush();
  b.calls.at(-1).resolve(state({messages:[{id:1,role:'user',kind:'reply',created:1,text:'請查網站'}]})); await flush();
  assert.equal(b.$('brain-input').value, '送出期間的新草稿', 'A successful old request cannot erase newer typing');
  b.frame(); b.calls.filter(c => c.path === '/api/brain/ack').forEach(c => c.resolve({ok:true})); await flush();
  assert.equal(b.scrollingElement.scrollTop, 2988, 'Initial load starts at the latest message');
  b.scrollingElement.scrollTop = 300;
  a.update(); b.calls.at(-1).resolve(state({messages:[{id:1,role:'user',kind:'reply',created:1,text:'請查網站'},{id:2,role:'assistant',kind:'result',created:2,text:'已查明，尚未修正。'}]})); await flush(); b.frame();
  assert.equal(b.scrollingElement.scrollTop, 300, 'Polling preserves reading older content');
  assert.equal(b.$('brain-messages').children.length, 2);
  // Approval forms survive unrelated heartbeat updates, retaining typed answers.
  const decision = {session:{id:'one',binding:'tty1',title:'網站',updatedAt:1},requests:[{id:'approval-1',state:'pending'}]};
  a.update(); b.calls.at(-1).resolve(state({decisions:[decision]})); await flush();
  const card = b.$('brain-decisions').children[0];
  a.update(); b.calls.at(-1).resolve(state({decisions:[{...decision,session:{...decision.session,updatedAt:2}}]})); await flush();
  assert.equal(b.$('brain-decisions').children[0], card);
  await a.openWork('one'); assert.deepEqual(b.opened, ['one']); assert.equal(a.mode(), 'manager');
  assert.equal(b.$('message').value, '管理畫面的草稿');
  // Dedicated Brain opens only on explicit click, and existing exact session is viewable.
  a.chooseMode('assistant'); b.calls.at(-1).resolve(state({backend:{kind:'codex',connected:false}})); await flush();
  const beforeOpen = b.calls.length;
  b.$('brain-open').click();
  assert.equal(b.calls.at(-1).path, '/api/brain/open');
  b.$('brain-open').click(); assert.equal(b.calls.length,beforeOpen+1);
  b.calls.at(-1).resolve({opened:true,session:'one'}); await flush();
  b.calls.at(-1).resolve(state({backend:{kind:'codex',connected:true,session:'one'}})); await flush();
  assert.equal(b.$('brain-open').textContent,'查看 Brain 對話 ↗');
  await b.$('brain-open').click(); assert.equal(a.mode(),'manager');
  // A response arriving after logout cannot resurrect another user's conversation.
  a.chooseMode('assistant'); const late = b.calls.at(-1); a.reset(); b.context.token = '';
  late.resolve(state({messages:[{id:9,role:'assistant',kind:'result',created:2,text:'PRIVATE'}]})); await flush();
  assert.equal(b.$('brain-messages').children.length, 0);
  // Saved thread URLs restore the manager, never mix a terminal into Brain chat.
  a.restore({mode:'assistant',view:'thread'}); assert.equal(a.mode(), 'manager');
  b.context.connected = false; b.$('brain-input').value = '保留'; a.refresh(); assert.equal(b.$('brain-send').disabled, true);
  assert.ok(!b.calls.some(c => ['/api/send','/api/ack'].includes(c.path)), 'Reading Brain never sends terminal text or acknowledges source work');
  console.log('Brain UI checks passed: unified conversation, exact retry, manager drafts, stable approvals, scrolling, logout and navigation.');
})().catch(error => {console.error(error); process.exitCode=1;});
module.exports = {browser, state, flush};
