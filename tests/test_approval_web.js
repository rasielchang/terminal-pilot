"use strict";
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {browser} = require('./test_navigation.js');
function setup() {
  const app = browser();
  const node = tag => ({tag, children:[], handlers:{}, textContent:'', value:'', hidden:false,
    append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children=children; },
    setAttribute() {}, addEventListener(name, callback) { this.handlers[name]=callback; },
    reportValidity() { return true; }, scrollIntoView() { this.scrolled=true; }});
  app.document.createElement = node;
  app.run(fs.readFileSync('web/approvals.js','utf8'));
  app.run(`connected=true; selected='a'; token='paired';
    sessions=[{id:'a',binding:'tty:a',tool:'codex',codexSocket:'/owned/socket',codexThreadID:'thread',runtime:{approvals:[{
      id:'consent-a',state:'pending',title:'Allow <literal>?',detail:'Original request',kind:'approval',
      server:'synthetic',context:[{label:'Command',text:'echo <test>'}],fields:[],actions:['accept','decline','cancel']
    }]}}]; var calls=[]; var sequence=0; requestID=()=>String(++sequence); poll=async()=>{};`);
  app.node('approval-panel').replaceChildren = function(...children) {this.children=children;};
  return app;
}
(async () => {
  const app=setup();
  app.run('window.PilotApprovals.update(current())');
  assert.equal(app.node('approval-panel').hidden,false);
  assert.equal(app.run('calls.length'),0);
  assert.equal(app.node('approval-panel').children[0].children[0].textContent,'Allow <literal>?');
  const approvalCard = app.node('approval-panel').children[0];
  approvalCard.getBoundingClientRect = () => ({top: 9000 - app.document.scrollingElement.scrollTop});
  app.document.scrollingElement.scrollHeight = 10000;
  app.run('readLatestMessage()');
  assert.equal(app.document.scrollingElement.scrollTop, 8888,
    'Opening the latest content prioritizes the bottom approval, below the sticky phone header');
  app.document.scrollingElement.scrollTop = 300;
  app.run('window.PilotApprovals.update(current())');
  assert.equal(app.document.scrollingElement.scrollTop, 300, 'Approval polling preserves older reading');
  assert.equal(app.node('approval-panel').children[0], approvalCard, 'Polling retains the existing form');
  app.run(`api=async(path,data)=>{calls.push({path,data});return {submitted:true,resolved:true};}`);
  await app.run(`window.PilotApprovals.submit(current(),current().runtime.approvals[0],'accept',{})`);
  assert.equal(app.run('calls.length'),1);
  assert.equal(app.run('calls[0].data.approval'),'consent-a');
  assert.equal(app.run('calls[0].data.binding'),'tty:a');
  await app.run(`window.PilotApprovals.submit(current(),current().runtime.approvals[0],'accept',{})`);
  assert.equal(app.run('calls.length'),1,'Double tap never submits another decision');
  app.run(`sessions[0].runtime.approvals=[];window.PilotApprovals.update(current());`);
  assert.equal(app.node('approval-panel').hidden,true,'Desktop/server resolution removes prompt');
  const offline=setup();
  offline.run(`connected=false;window.PilotApprovals.update(current());`);
  const form=offline.node('approval-panel').children[0].children.at(-1);
  assert.ok(form.children[0].children.every(b=>b.disabled));
  await offline.run(`window.PilotApprovals.submit(current(),current().runtime.approvals[0],'accept',{})`);
  assert.equal(offline.run('calls.length'),0);
  const uncertain=setup();
  uncertain.run(`api=async(path,data)=>{calls.push({path,data});throw new Error('Disconnected');}`);
  await uncertain.run(`window.PilotApprovals.submit(current(),current().runtime.approvals[0],'decline',{})`);
  await uncertain.run(`window.PilotApprovals.submit(current(),current().runtime.approvals[0],'decline',{})`);
  assert.equal(uncertain.run('calls.length'),1,'Ambiguous delivery must not silently resend');
  const trust=setup();
  trust.run(`sessions[0].tool='claude';sessions[0].runtime.approvals[0].kind='workspace_trust';
    sessions[0].runtime.approvals[0].actions=['accept','decline'];window.PilotApprovals.update(current());`);
  const trustButtons=trust.node('approval-panel').children[0].children.at(-1).children[0].children;
  assert.deepEqual(trustButtons.map(b=>b.textContent),['同意並信任此資料夾','不同意並離開']);
  assert.equal(trust.run('calls.length'),0,'Displaying trust is never consent');
  trust.run(`api=async(path,data)=>{calls.push({path,data});return {submitted:true};}`);
  trustButtons[0].handlers.click(); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(trust.run('calls[0].path'),'/api/approval');
  assert.equal(trust.run('calls[0].data.action'),'accept');
  assert.equal(trust.run('calls[0].data.binding'),'tty:a');
  const startup=setup();
  startup.run(`sessions[0].codexThreadID='';sessions[0].runtime.approvals=[{
    id:'startup:exact',kind:'startup_menu',state:'pending',title:'Codex 等待啟動確認',
    context:[{label:'原始確認',text:'Hooks need review <literal>'},{label:'Hook',text:'echo <safe>'}],
    actions:['choice_2','choice_3'],actionLabels:{choice_2:'信任以上 hooks 並繼續',choice_3:'不信任並繼續'}
  }];window.PilotApprovals.update(current());`);
  const startupButtons=startup.node('approval-panel').children[0].children.at(-1).children[0].children;
  assert.deepEqual(startupButtons.map(b=>b.textContent),['信任以上 hooks 並繼續','不信任並繼續']);
  assert.equal(startup.node('approval-upgrade').hidden,true,'Pre-thread work must not require resume');
  assert.equal(startup.run('calls.length'),0);
  startup.run(`api=async(path,data)=>{calls.push({path,data});return {submitted:true};}`);
  startupButtons[1].handlers.click();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(startup.run('calls[0].data.action'),'choice_3');
  assert.equal(startup.run('calls[0].data.approval'),'startup:exact');
  assert.equal(startup.run('calls[0].path'),'/api/approval');
  console.log('Approval UI checks passed: original text, startup menus, explicit choice, exact target, double tap, offline, resolution and ambiguous delivery.');
})().catch(error=>{console.error(error);process.exitCode=1;});
