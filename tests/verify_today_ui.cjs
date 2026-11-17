// Optional browser acceptance against tests/today_fixture.py; never a live store.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const cfg=JSON.parse(fs.readFileSync(path.join(process.argv[2],'fixture.json')));
  const evidence=process.argv[3];fs.mkdirSync(evidence,{recursive:true});
  const browser=await chromium.launch({headless:true,channel:process.env.PILOT_BROWSER_CHANNEL || undefined});
  const context=await browser.newContext({viewport:{width:390,height:844}});
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try {
    await page.goto(cfg.url);
    await page.evaluate(t=>{sessionStorage.setItem('pilot-token',t);localStorage.setItem('pilot-view','manager');},cfg.token);
    await page.goto(cfg.url);
    await page.locator('.today-card').first().waitFor();
    assert.equal(await page.locator('#brain-conversation').isVisible(),false);
    for(const group of await page.locator('.today-tracking-group').all())assert.equal(await group.getAttribute('open'),null);
    assert.equal(await page.locator('.today-completed').getAttribute('open'),null);
    const overflow=()=>page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
    assert.deepEqual(await overflow(),{page:390,viewport:390});
    const meeting=page.locator('.today-required [data-card$="meeting.json"]');
    assert.equal(await meeting.locator('h3').isVisible(),true);
    const meetingTitle=await meeting.locator('h3').boundingBox();assert.ok(meetingTitle.y+meetingTitle.height<844,'Rank 2 title must be visible on the first mobile screen');
    assert.equal(await meeting.locator('.today-state').textContent(),'待做');
    assert.equal(await meeting.getByRole('button',{name:'開啟專員 ↗'}).count(),0);
    assert.equal(await page.locator('.today-tracking [data-card$="meeting.json"]').count(),0);
    assert.equal(await page.locator('.today-optional [data-card$="optional.json"]').count(),1);
    assert.equal(await page.locator('.today-important [data-card$="due_blocked.json"] .today-blocker').isVisible(),true);
    assert.ok((await page.locator('.today-required>h2').boundingBox()).y<400);
    await page.screenshot({path:path.join(evidence,'mobile-first-screen.png')});
    await page.screenshot({path:path.join(evidence,'mobile.png'),fullPage:true});
    const expected=['design','interface','waiting','done','small','external','scheduled','weekly','future','meeting','optional','due_blocked','answered'].map(n=>'projects/demo/work-items/'+n+'.json').sort();
    const ids=await page.locator('[data-card]').evaluateAll(cards=>cards.map(c=>c.dataset.card).sort());
    assert.deepEqual(ids,expected);assert.equal(new Set(ids).size,ids.length);
    assert.match(await page.locator('.today-tracking>h2').textContent(),/其他追蹤 · 6/);
    for(const group of await page.locator('.today-tracking-group').all()) {
      await group.locator(':scope > summary').click();
      for(const card of await group.locator('[data-card]').all()) {
        await card.locator(':scope > summary').click();
        assert.equal(await card.locator('.today-next').isVisible(),true);
        assert.equal(await card.locator('.today-source>summary').isVisible(),true);
        assert.deepEqual(await overflow(),{page:390,viewport:390});
      }
    }
    await page.screenshot({path:path.join(evidence,'mobile-tracking-expanded.png'),fullPage:true});
    // A new canonical reply/completion removes an old unselected wait on poll.
    const externalPath=path.join(cfg.root,'brain/projects/demo/work-items/external.json');
    const external=JSON.parse(fs.readFileSync(externalPath));
    external.status='completed';external.completed_at=new Date().toISOString();external.waiting_on_user=null;
    external.result='已核對較新回覆';external.updated_at=new Date().toISOString();fs.writeFileSync(externalPath,JSON.stringify(external));
    await page.waitForFunction(()=>document.querySelector('.today-tracking>h2').textContent==='其他追蹤 · 5');
    assert.equal(await page.locator('.today-tracking [data-card$="external.json"]').count(),0);
    assert.equal(await page.locator('.today-completed [data-card$="external.json"]').count(),1);
    assert.equal(await page.locator('[data-section="tracking-scheduled"]').getAttribute('open'),'');
    await page.locator('#assistant-chat').click();
    await page.locator('#brain-input').fill('保留聊天草稿');
    assert.equal(await page.locator('#brain-messages').isVisible(),true);
    await page.locator('#assistant-today').click();
    await page.locator('#assistant-chat').click();
    assert.equal(await page.locator('#brain-input').inputValue(),'保留聊天草稿');
    await page.locator('#assistant-today').click();
    await page.locator('[data-card$="design.json"]').getByRole('button',{name:'開啟專員 ↗'}).click();
    await page.getByText('精確專員：sample-a',{exact:true}).waitFor();
    await page.locator('#brain-return').click();
    await page.locator('#today-panel').waitFor({state:'visible'});
    // Canonical file changes flow through the real server, not a mocked route.
    const cardPath=path.join(cfg.root,'brain/projects/demo/work-items/interface.json');
    let card=JSON.parse(fs.readFileSync(cardPath));
    card.waiting_on_user={action:'確認隔離樣本',source_at:Date.now()/1000,release_condition:'回覆確認'};
    card.updated_at=new Date().toISOString();fs.writeFileSync(cardPath,JSON.stringify(card));
    await page.waitForFunction(()=>document.querySelector('[data-card$="interface.json"] .today-state').textContent==='需要你處理');
    card.status='completed';card.completed_at=new Date().toISOString();card.waiting_on_user=null;fs.writeFileSync(cardPath,JSON.stringify(card));
    await page.waitForFunction(()=>document.querySelector('.today-completed').textContent.includes('介面調整與回歸驗證'));
    assert.equal(await page.locator('.today-required').getByText('介面調整與回歸驗證',{exact:true}).count(),0);
    await page.setViewportSize({width:1440,height:1000});
    assert.deepEqual(await overflow(),{page:1440,viewport:1440});
    await page.screenshot({path:path.join(evidence,'desktop.png'),fullPage:true});
    await page.locator('#assistant-chat').click();
    await page.goto(cfg.url); // PWA start_url is '/', regardless of prior page preference.
    await page.locator('#today-panel').waitFor({state:'visible'});
    assert.equal(await page.locator('#brain-conversation').isVisible(),false);
    await context.setOffline(true);
    await page.waitForFunction(()=>document.querySelector('.today-alert')?.textContent.includes('連線中斷'));
    assert.ok(await page.locator('#today-panel').textContent().then(t=>t.includes('確認首頁設計')));
    await context.setOffline(false);
    await page.waitForFunction(()=>!document.querySelector('.today-alert'));
    await page.goto(cfg.url+'/?work=sample-b');
    await page.getByText('精確專員：sample-b',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    const result={mobile:390,desktop:1440,overflow:false,defaultPanel:true,chatDraft:true,exactWorker:true,
      allTrackingMapped:true,uniqueCardCounts:true,collapsedGroups:true,expandedDetails:true,liveTrackingCompletion:true,
      realFileTransitions:true,offlineRetention:true,deepLink:true,browserErrors:errors};
    fs.writeFileSync(path.join(evidence,'ui-result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
