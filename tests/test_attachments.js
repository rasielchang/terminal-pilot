'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const calls=[],shared=[],revoked=[],timers=[];let auth='test-token',responseCode=200;
const raw=Buffer.from('\ufeffid,name\r\n1,synthetic\r\n');
function node(tag){return {tag,children:[],handlers:{},append(...v){this.children.push(...v)},prepend(...v){this.children.unshift(...v)},replaceChildren(...v){this.children=v},setAttribute(){},addEventListener(k,v){this.handlers[k]=v}}}
class TestURL extends URL {}
TestURL.createObjectURL=()=> 'blob:private';TestURL.revokeObjectURL=u=>revoked.push(u);
const context=vm.createContext({window:{},document:{createElement:node,createTextNode:t=>({textContent:t})},URL:TestURL,File,Blob,
 navigator:{canShare:()=>true,share:async value=>shared.push(value)},setTimeout:f=>timers.push(f),
 fetch:async(url,options)=>{calls.push({url,options});return {ok:responseCode===200,status:responseCode,headers:new Headers({'Content-Type':'text/csv; charset=utf-8','Content-Disposition':"attachment; filename*=UTF-8''synthetic.csv"}),blob:async()=>new Blob([raw])}}});
vm.runInContext(fs.readFileSync('web/attachments.js','utf8'),context);
const ui=context.window.PilotAttachments,route='/api/attachments?id='+'a'.repeat(32)+'&topic=task-1&target=conversation%3A'+'b'.repeat(32);
(async()=>{
 for(const bad of ['https://evil.invalid'+route,'//evil.invalid'+route,route+'&path=/etc/passwd',route+'&id=duplicate',route+'#x','/api/photos?id=x'])assert.equal(ui.path(bad),null);
 const box=ui.link('取得CSV',route,()=>auth);await box.children[0].handlers.click();
 assert.equal(calls[0].options.headers.Authorization,'Bearer test-token');assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.redirect,'error');assert.ok(!calls[0].url.includes('test-token'));
 assert.equal(box.children[0].textContent,'儲存至檔案／分享');await box.children[0].handlers.click();
 assert.deepEqual(Buffer.from(await shared[0].files[0].arrayBuffer()),raw);assert.equal(shared[0].files[0].name,'synthetic.csv');
 assert.equal(box.children[1].download,'synthetic.csv');assert.equal(box.children[1].href,'blob:private');timers[0]();assert.deepEqual(revoked,['blob:private']);
 responseCode=401;const rejected=ui.link('下載',route,()=>auth);await rejected.children[0].handlers.click();assert.match(rejected.children[1].textContent,/重新配對/);assert.equal(rejected.children[0].disabled,false);
 auth='';await rejected.children[0].handlers.click();assert.equal(calls.length,2);
 const html=fs.readFileSync('web/index.html','utf8');assert.ok(html.indexOf('/attachments.js')<html.indexOf('/app.js'));
 console.log('Private attachment UI: same-origin auth, exact bytes, separate share gesture, fallback download, expiry and unauthorized errors passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
