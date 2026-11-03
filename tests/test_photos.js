'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function harness(){
  const calls=[],revoked=[];let serial=0;
  function node(tag){return {tag,children:[],append(...v){this.children.push(...v);},replaceChildren(...v){this.children=v;},setAttribute(){},click(){this.onclick?.();}};}
  class Reader{readAsDataURL(){this.result='data:image/png;base64,cGljdHVyZQ==';queueMicrotask(()=>this.onload());}}
  const context=vm.createContext({window:{},document:{createElement:node},URL:{createObjectURL:()=>`blob:${++serial}`,revokeObjectURL:u=>revoked.push(u)},FileReader:Reader,
    requestID:()=>`request-${++serial}`,api:(path,data)=>new Promise((resolve,reject)=>calls.push({path,data,resolve,reject}))});
  vm.runInContext(fs.readFileSync('web/photos.js','utf8'),context);
  const container=node('div'),photos=context.window.PilotPhotos,c=photos.composer(container),input=container.children[1];
  return {c,photos,calls,container,revoked,choose(files){input.files=files;input.onchange();}};
}
const file={name:'photo.png',type:'image/png',size:8},a={session:'one',binding:'one-binding',thread:'thread-one'},b={session:'two',binding:'two-binding',thread:'thread-two'};
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
(async()=>{
  const h=harness();h.c.setTarget(a);h.choose([file]);await flush();assert.equal(h.calls.length,1);assert.equal(h.c.ready(),false);
  h.calls[0].reject(new TypeError('load failed'));await flush();assert.equal(h.c.ready(),false);
  const card=h.container.children[2].children[0];card.children.find(c=>c.textContent==='重試上傳').onclick();await flush();
  assert.deepEqual(h.calls[1].data,h.calls[0].data,'Upload retry uses exactly the same request identity and target');
  h.c.setTarget(b);assert.equal(h.c.has(),false);h.calls[1].resolve({photo:{id:'a'.repeat(32)}});await flush();assert.equal(h.c.has(),false,'Late upload cannot move to another session');
  h.c.setTarget(a);assert.equal(h.c.ready(),true);assert.equal(h.c.ids()[0],'a'.repeat(32));
  const captured=h.c.capture(),attempt=h.c.attempt('Look');assert.equal(h.c.attempt('Look'),attempt);assert.throws(()=>h.c.attempt('changed'),/尚未確認/);
  h.c.setTarget(b);h.choose([file]);await flush();h.calls[2].resolve({photo:{id:'b'.repeat(32)}});await flush();
  h.c.clear(captured);assert.equal(h.c.has(),true,'Old send success preserves other-session photo draft');assert.equal(h.c.ids()[0],'b'.repeat(32));
  h.c.setTarget(a);assert.equal(h.c.has(),false);h.c.setTarget(b);h.c.lock(true);assert.equal(h.container.children[0].disabled,true);
  h.choose([file]);await flush();assert.equal(h.calls.length,3,'Locked Brain submission cannot change photo payload');
  h.c.lock(false);h.c.clear();assert.equal(h.c.has(),false);assert.ok(h.revoked.length>=2);
  h.choose([file]);h.photos.reset();await flush();assert.equal(h.calls.length,3,'Logout cancels uploads not yet issued');h.c.setTarget(b);assert.equal(h.c.has(),false);
  h.choose([{name:'movie.mov',type:'video/quicktime',size:3}]);await flush();assert.equal(h.c.ready(),false);assert.match(h.container.children[2].children[0].children[1].textContent,/不支援影片/);assert.equal(h.calls.length,3);
  h.c.clear();h.choose([{...file,size:13*1024*1024}]);await flush();assert.match(h.container.children[2].children[0].children[1].textContent,/12MB/);
  console.log('Photo UI: exact upload retry, session isolation, pending-send fence, draft preservation, logout, remove and limits passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
