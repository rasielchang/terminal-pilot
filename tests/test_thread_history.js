'use strict';
const assert=require('node:assert/strict');
const {browser}=require('./test_navigation.js');
(async()=>{
 const b=browser();b.node('send').firstChild={textContent:''};
 b.run(`selected='a';token='paired';connected=true;sessions=[{id:'a',binding:'first',tool:'codex',canSend:true}];loadThread=originalLoadThread;
   globalThis.requests=[];globalThis.responses=[];api=async path=>{requests.push(path);return responses.shift();};
   globalThis.page=(thread,revision,messages)=>({session:{...sessions[0]},thread,revision,messages,source:'codex',cursor:messages.at(-1)?.cursor || 3,hasMore:true});
   responses.push(page('original','r1',[{id:'two',cursor:2,role:'assistant',text:'second'}]));`);
 await b.run('loadThread()');
 b.run(`responses.push(page('original','r1',[{id:'three',cursor:3,role:'assistant',text:'third'}]));`);
 await b.run('loadThread()');assert.match(b.run('requests.at(-1)'),/after=2/);assert.equal(b.node('messages').children.length,2);
 b.run(`responses.push(page('original','r1',[{id:'one',cursor:1,role:'user',text:'first'}]));responses.push(page('original','r1',[]));`);
 await b.node('thread-earlier').handlers.click();assert.equal(b.node('messages').children.length,3);
 b.run(`responses.push(page('different','r2',[]));responses.push(page('different','r2',[{id:'new',cursor:1,role:'assistant',text:'new thread'}]));`);
 await b.run('loadThread()');assert.equal(b.node('messages').children.length,1);assert.equal(b.run('threadPages.get("a").thread'),'different');assert.equal(b.run('requests.at(-1)'),'/api/thread?session=a');
 console.log('Full worker history: incremental pages, earlier page retained, different thread/revision forces fresh history passed.');
})().catch(e=>{console.error(e);process.exit(1)});
