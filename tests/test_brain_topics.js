'use strict';
const assert = require('node:assert/strict');
const {browser,state,flush} = require('./test_assistant');
const topics = [{id:'a',title:'手機斷線',goal:'查明斷線',state:'open'}, {id:'b',title:'退款查詢',goal:'查退款',state:'open'}];
const scoped = (id, messages=[], extra={}) => state({topic:topics.find(t => t.id===id) || {id:'',title:'主對話'},topics,messages,...extra});
const message = (id,text) => ({id,text,role:'assistant',created:1,kind:'result'});
const submit = b => b.$('brain-compose').handlers.submit({preventDefault(){}});
const input = (b,text) => { b.$('brain-input').value=text; b.$('brain-input').handlers.input(); };
const respond = async (b,value) => { b.calls.at(-1).resolve(value); await flush(); };
(async () => {
  const b=browser(), a=b.assistant;
  a.update(); await respond(b,scoped('',[message(1,'主對話')], {topics:topics.map(t => ({...t, unread:t.id==='a'}))})); b.frame();
  assert.match(b.$('brain-scope').textContent,/1 件未讀提醒/, 'Badge source remains discoverable beside the composer');
  input(b,'主對話草稿'); b.scrollingElement.scrollTop=123;
  a.switchTopic('a'); assert.equal(b.calls.at(-1).path,'/api/brain?topic=a');
  await respond(b,scoped('a',[message(2,'A 的討論')])); b.frame();
  assert.match(b.$('brain-scope').textContent,/手機斷線/);
  assert.doesNotMatch(b.$('brain-scope').textContent,/未讀提醒/);
  input(b,'A 草稿'); b.scrollingElement.scrollTop=456;
  a.switchTopic('b'); await respond(b,scoped('b',[message(3,'B 的討論')])); b.frame();
  input(b,'B 草稿');
  a.switchTopic('a'); await respond(b,scoped('a',[message(2,'A 的討論')])); b.frame();
  assert.equal(b.$('brain-input').value,'A 草稿'); assert.equal(b.scrollingElement.scrollTop,456);
  assert.equal(b.$('brain-messages').children.length,1);
  assert.equal(b.$('brain-messages').children[0].children[1].textContent,'A 的討論');
  assert.equal(b.context.history.state.pilot.topic,'a');
  // A send in flight can finish while B has its own composer, without clearing B.
  submit(b); const sentA=b.calls.at(-1); assert.equal(sentA.data.topic,'a');
  a.switchTopic('b'); await respond(b,scoped('b',[message(3,'B 的討論')]));
  assert.equal(b.$('brain-input').value,'B 草稿'); assert.equal(b.$('brain-send').disabled,false);
  sentA.resolve({accepted:true}); await flush();
  assert.equal(b.$('brain-input').value,'B 草稿');
  // Ambiguous delivery stays bound to B, even after switching away/back.
  submit(b); const sentB=b.calls.at(-1); sentB.reject(new TypeError('load failed')); await flush();
  a.switchTopic('a'); await respond(b,scoped('a',[message(2,'A 的討論')]));
  assert.equal(b.$('brain-input').value,''); assert.equal(b.$('brain-error').textContent,'');
  a.switchTopic('b'); await respond(b,scoped('b',[message(3,'B 的討論')]));
  assert.match(b.$('brain-send').textContent,/確認送出/); submit(b);
  assert.deepEqual(b.calls.at(-1).data,sentB.data);
  b.calls.at(-1).resolve({accepted:true}); await flush(); await respond(b,scoped('b',[message(3,'B 的討論')]));
  // Late reads cannot paint another scope, and arrivals never navigate away.
  a.update(); const stale=b.calls.at(-1);
  a.switchTopic('a'); await respond(b,scoped('a',[message(2,'A 的討論')],{topics:[topics[0],{...topics[1],unread:true}]}));
  stale.resolve(scoped('b',[message(99,'不可顯示')])); await flush();
  assert.equal(a.topic(),'a'); assert.equal(b.$('brain-updates').hidden,false);
  assert.equal(b.$('brain-messages').children[0].children[1].textContent,'A 的討論');
  b.frame(); const acknowledgments=b.calls.filter(c=>c.path==='/api/brain/ack');
  assert.ok(acknowledgments.some(c=>c.data.topic==='a'));
  // Source reading stays within Brain and attaches an ID, not pasted transcript.
  const job={id:'job-a',topic:'a',session:'one',title:'網站',state:'completed',label:'已回報'};
  a.update(); await respond(b,scoped('a',[message(2,'A 的討論')],{jobs:[job]}));
  const actions=b.$('brain-jobs').children[0].children.at(-1);
  actions.children[0].click(); assert.equal(b.calls.at(-1).path,'/api/brain/source?job=job-a');
  await respond(b,{instruction:'查斷線',result:'很長的完整回報',available:true,messages:[]});
  assert.equal(a.mode(),'assistant'); assert.equal(b.$('brain-source').hidden,false);
  const quote=b.$('brain-source-messages').children.find(c=>c.tag==='button'); quote.click();
  assert.equal(b.$('brain-source').hidden,true); assert.ok(!b.$('brain-input').value.includes('很長的完整回報'));
  submit(b); assert.equal(b.calls.at(-1).data.source,'job-a'); assert.equal(b.calls.at(-1).data.topic,'a');
  b.calls.at(-1).resolve({accepted:true}); await flush(); await respond(b,scoped('a',[message(2,'A 的討論')]));
  // Main draft survives reading sources and the raw manager still opens exact sessions.
  a.switchTopic(''); await respond(b,scoped('',[message(1,'主對話')])); b.frame();
  assert.equal(b.$('brain-input').value,'主對話草稿'); assert.equal(b.scrollingElement.scrollTop,123);
  await a.openWork('one'); assert.equal(a.mode(),'manager'); assert.deepEqual(b.opened,['one']);
  // A desktop reader has two separate scroll positions, starting the source at top.
  const d=browser(), da=d.assistant; d.context.mobileLayout.matches=false; d.context.window.innerWidth=1400;
  da.update(); await respond(d,scoped('',[message(1,'主對話')],{jobs:[job]})); d.frame();
  d.$('assistant-home').scrollTop=700;
  d.$('brain-jobs').children[0].children.at(-1).children[0].click();
  assert.equal(d.$('assistant-home').scrollTop,0);
  assert.equal(d.$('brain-conversation').scrollTop,700);
  assert.equal(d.$('brain-source').scrollTop,0);
  await respond(d,{instruction:'查明',result:'結果',available:true,messages:[]});
  d.$('brain-source-close').click(); d.frame();
  assert.equal(d.$('assistant-home').scrollTop,700);
  console.log('Matter UI checks passed: independent drafts/scroll, concurrent sends, scoped retry/ack, stale responses, source reader/reference, manager unchanged.');
})().catch(error=>{console.error(error);process.exitCode=1;});
