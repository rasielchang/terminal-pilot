// Optional actual Chrome UI -> shipped CLI -> report -> independent closeout fixture.
const {spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  for(const width of [390,1440]){
   const output=path.resolve(process.argv[2],String(width));fs.mkdirSync(output,{recursive:true});
   const p=spawn(process.env.PILOT_TEST_PYTHON||'python3',['-B',path.join(__dirname,'serve_local_closeout_ui.py'),output]);
   let errors='';p.stderr.on('data',x=>errors+=x);
   try{
    const ready=await new Promise((resolve,reject)=>{let buf='';p.stdout.on('data',x=>{buf+=x;if(buf.includes('\n'))resolve(JSON.parse(buf.split('\n')[0]));});p.on('exit',code=>reject(new Error('fixture exited '+code+' '+errors)));});
    const context=await browser.newContext({viewport:{width,height:width===390?844:1000}});
    await context.addInitScript(()=>{sessionStorage.setItem('pilot-token','synthetic-only');localStorage.setItem('pilot-token','synthetic-only');localStorage.setItem('pilot-mode','manager');});
    const page=await context.newPage(),browserErrors=[];page.on('pageerror',e=>browserErrors.push(e.message));
    await page.goto(ready.origin+'/#work='+ready.session);
    await page.locator('#message').waitFor({state:'visible'});
    await page.locator('#message').fill(width===390?'回報給 Brain 並結案':'這件驗收完了。跟 Brain 說一聲，把它畫上句點吧。');
    await page.locator('#send').click();
    const deadline=Date.now()+30000;
    while(!fs.existsSync(path.join(output,'result.json'))&&Date.now()<deadline)await new Promise(r=>setTimeout(r,150));
    assert.ok(fs.existsSync(path.join(output,'result.json')),errors);
    const result=JSON.parse(fs.readFileSync(path.join(output,'result.json')));
    assert.deepEqual(result.brainTurns,['explicit_report','authorized_continuation']);
    assert.equal(result.closedCount,1);assert.equal(result.unrelatedWorkPreserved,true);
    assert.equal(result.report.delivered,true);assert.equal(result.report.summarized,true);
    assert.equal(result.continuation.state,'completed');assert.equal(result.continuation.summarized,true);
    await page.goto(ready.origin+'/continuation.html#'+result.continuation.id);
    await page.getByText('已完成，Brain 已統整',{exact:false}).waitFor();
    assert.equal(await page.locator('#confirm').count(),0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(browserErrors,[]);
    await page.screenshot({path:path.join(output,'receipt.png'),fullPage:true});
    console.log(JSON.stringify({width,report:true,independentContinuation:true,closedReceipt:true,replayOnce:true,noConsentClick:true,browserErrors}));
    await context.close();
   }finally{p.kill('SIGTERM');}
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
