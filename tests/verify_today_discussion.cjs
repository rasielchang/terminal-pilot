// Real HTTP + synthetic Brain queue; requires today_fixture.py --discussions.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const cfg=JSON.parse(fs.readFileSync(path.join(process.argv[2],'fixture.json')));
  const output=process.argv[3];fs.mkdirSync(output,{recursive:true});
  const baseline=fs.existsSync(path.join(cfg.root,'delivery.json')) ? JSON.parse(fs.readFileSync(path.join(cfg.root,'delivery.json'))).deliveries.length : 0;
  const browser=await chromium.launch({headless:true,channel:process.env.PILOT_BROWSER_CHANNEL || undefined});
  const errors=[],deliveries=[];
  try {
    for(const width of [390,1440]) {
      const context=await browser.newContext({viewport:{width,height:width===390?844:1000}});
      await context.addInitScript(t=>sessionStorage.setItem('pilot-token',t),cfg.token);
      const page=await context.newPage(),posts=[];
      page.on('pageerror',e=>errors.push(e.message));
      page.on('request',r=>{if(r.method()==='POST')posts.push({url:new URL(r.url()).pathname,data:r.postDataJSON()});});
      await page.goto(cfg.url);await page.locator('.today-card').first().waitFor();
      const rows=page.locator('[data-card]'), ids=await rows.evaluateAll(items=>items.map(i=>i.dataset.card));
      assert.equal(await page.getByRole('button',{name:'跟 Brain 討論',exact:true,includeHidden:true}).count(),ids.length);
      const scroll=()=>page.evaluate(()=> (matchMedia('(max-width: 720px)').matches?document.scrollingElement:document.querySelector('#assistant-home')).scrollTop);
      const settle=()=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
      const openCard=async id=>{
        const row=page.locator(`[data-card="${id}"]`);
        await row.evaluate(el=>{for(let p=el;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});
        await row.getByRole('button',{name:'跟 Brain 討論',exact:true}).scrollIntoViewIfNeeded();
        return row;
      };
      await page.locator('#assistant-chat').click();await page.locator('#brain-input').fill('原本草稿，還要討論另一個任務');
      await page.locator('#assistant-today').click();
      for(const id of ids) {
        const row=await openCard(id);await settle();const top=await scroll();
        const before=posts.filter(p=>p.url==='/api/brain/message').length;
        await row.getByRole('button',{name:'跟 Brain 討論',exact:true}).click();
        await page.locator('#brain-reference').waitFor({state:'visible'});
        await page.waitForFunction(()=>document.querySelector('#brain-reference-label').textContent.includes('僅提供脈絡'));
        assert.ok((await page.locator('#brain-reference-label').textContent()).includes(id));
        assert.equal(await page.locator('#brain-input').inputValue(),'原本草稿，還要討論另一個任務');
        assert.equal(posts.filter(p=>p.url==='/api/brain/message').length,before,'Selection cannot send');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        await page.locator('#assistant-today').click();await settle();
        assert.ok(Math.abs(await scroll()-top)<4,`Return position changed for ${width}/${id}: ${top} -> ${await scroll()}`);
        assert.equal(await row.getByRole('button',{name:'跟 Brain 討論',exact:true}).isVisible(),true);
      }
      // Browser back/forward preserves the panel position too.
      const a=await openCard(ids[0]);await settle();const top=await scroll();
      await a.getByRole('button',{name:'跟 Brain 討論',exact:true}).click();
      await page.goBack();await settle();assert.ok(Math.abs(await scroll()-top)<4);
      await page.goForward();await page.locator('#brain-reference-clear').click();
      assert.equal(await page.locator('#brain-reference').isVisible(),false);
      assert.equal(await page.locator('#brain-input').inputValue(),'原本草稿，還要討論另一個任務');
      // Submit an explicit discussion from each region. Completed card's worker
      // is closed; the tracking card has never had a Pilot binding.
      for(const name of ['design','external','done']) {
        await page.locator('#assistant-today').click();
        const id=`projects/demo/work-items/${name}.json`,row=await openCard(id);
        await row.getByRole('button',{name:'跟 Brain 討論',exact:true}).click();
        const text=`${width}/${name} 先討論這件事，也談另一件事；不要開工`;
        await page.locator('#brain-input').fill(text);
        await page.screenshot({path:path.join(output,`${width}-${name}-reference.png`),fullPage:true});
        await page.locator('#brain-send').click();
        await page.getByText(text,{exact:true}).waitFor();
        const sent=posts.filter(p=>p.url==='/api/brain/message').at(-1);
        assert.equal(sent.data.referenceCard,id);assert.equal(sent.data.text,text);
        assert.deepEqual(Object.keys(sent.data).sort(),['referenceCard','requestID','text']);
        deliveries.push({text,id});
      }
      // Deleted source is shown explicitly, never replaced by a same-project card.
      await page.locator('#assistant-today').click();
      const staleID='projects/demo/work-items/weekly.json', stale=await openCard(staleID);
      await stale.getByRole('button',{name:'跟 Brain 討論',exact:true}).click();
      const file=path.join(cfg.root,'brain',staleID),raw=fs.readFileSync(file);fs.unlinkSync(file);
      await page.waitForFunction(()=>document.querySelector('#brain-reference-label').textContent.includes('來源已失效'));
      assert.ok((await page.locator('#brain-reference-label').textContent()).includes(staleID));
      fs.writeFileSync(file,raw);
      await page.locator('#brain-reference-clear').click();
      assert.equal(await page.locator('#brain-reference').isVisible(),false);
      assert.ok(posts.every(p=>['/api/brain/ack','/api/brain/message','/api/diagnostics/styles'].includes(p.url)),JSON.stringify(posts.map(p=>p.url)));
      await context.close();
    }
    const evidence=JSON.parse(fs.readFileSync(path.join(cfg.root,'delivery.json')));
    assert.equal(evidence.thread,'brain-thread');assert.equal(evidence.deliveries.length,baseline+6);
    for(let i=0;i<deliveries.length;i++) {
      assert.equal(evidence.deliveries[baseline+i].text,deliveries[i].text);
      assert.equal(evidence.deliveries[baseline+i].reference.canonicalID,deliveries[i].id);
      assert.equal(evidence.deliveries[baseline+i].scope,'');
    }
    assert.equal(evidence.jobs,0);assert.equal(evidence.topics,0);assert.deepEqual(evidence.nativeActions,[]);assert.deepEqual(errors,[]);
    const result={widths:[390,1440],allThreeRegions:true,unbound:true,closedWorker:true,allCards:9,deliveries:6,
      sameBrain:true,noWorkerActions:true,noAutoSend:true,exactReferences:true,cancel:true,draft:true,returnPosition:true,historyNavigation:true,missingSource:true,browserErrors:errors};
    fs.writeFileSync(path.join(output,'discussion-result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
