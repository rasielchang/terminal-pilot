// Isolated browser acceptance with fixture content and a loopback-only server.
"use strict";
const assert = require("node:assert/strict");
const {once} = require("node:events");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const {chromium} = require("playwright");

const assets = process.env.PILOT_WEB_ASSETS || path.join(__dirname, "../web");
const types = {".html":"text/html", ".js":"text/javascript", ".css":"text/css",
  ".svg":"image/svg+xml", ".png":"image/png", ".webmanifest":"application/manifest+json"};
const fixture = {id:"fixture", title:"隔離工作", directory:"/fixture", tool:"codex",
  binding:"fixture-binding", canSend:true, alive:true, phase:"idle"};
let title = fixture.title, staleState = null, pendingSend = null, holdState = false, sends = 0, stateReads = 0;
const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  const reply = (data, status = 200) => {
    res.writeHead(status, {"Content-Type":"application/json", "Cache-Control":"no-store"});
    res.end(JSON.stringify(data));
  };
  if (url.pathname === "/api/state") {
    stateReads++;
    if (holdState) { holdState = false; staleState = () => reply({sessions:[{...fixture, title:"過時工作"}]}); return; }
    return reply({sessions:[{...fixture, title}]});
  }
  if (url.pathname === "/api/thread") return reply({session:{...fixture, title}, source:"codex",
    messages:[{id:1, time:"2026-11-25T10:00:00Z", role:"assistant", text:"最後有效對話"}]});
  if (url.pathname === "/api/send") { sends++; pendingSend = () => reply({sent:true}); req.resume(); return; }
  if (url.pathname === "/api/diagnostics/styles") return reply({recorded:true});
  if (url.pathname.startsWith("/api/")) return reply({error:"unexpected fixture request"}, 404);
  const name = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
  if (!/^[\w.-]+$/.test(name) || !fs.existsSync(path.join(assets, name))) return reply({}, 404);
  res.writeHead(200, {"Content-Type": types[path.extname(name)], "Cache-Control":"no-store"});
  res.end(fs.readFileSync(path.join(assets, name)));
});

(async () => {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const url = `http://127.0.0.1:${server.address().port}/`;
  let browser;
  try {
    browser = await chromium.launch({channel: process.env.PILOT_BROWSER_CHANNEL || undefined});
    const context = await browser.newContext({viewport:{width:390, height:844}});
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.setDefaultTimeout(8000);
    await page.addInitScript(() => {
      if (location.pathname !== "/") return;
      const schedule = window.setInterval;
      window.setInterval = (handler, delay, ...args) => {
        const timer = schedule(handler, delay, ...args);
        if (handler.name === "poll") window.stopPilotPollTimer = () => clearInterval(timer);
        return timer;
      };
      sessionStorage.setItem("pilot-token", "fixture-only");
      localStorage.setItem("pilot-view", "manager");
    });
    await page.goto(url + "?work=fixture");
    await page.waitForFunction(() => navigator.serviceWorker.controller !== null);
    await page.getByText("最後有效對話", {exact:true}).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);

    // Stop timers briefly so this test owns the exact request order.
    await page.evaluate(() => window.stopPilotPollTimer());
    await page.waitForFunction(() => stateRead === null);
    holdState = true;
    await page.evaluate(() => { poll(); });
    await assertUntil(() => staleState !== null);

    await page.locator("#message").fill("隔離送出");
    await page.locator("#send").click();
    await assertUntil(() => pendingSend !== null);

    const before = stateReads;
    title = "恢復後工作";
    await page.evaluate(() => {
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted:true}));
      window.dispatchEvent(new Event("online"));
    });
    await page.waitForFunction(expected => document.querySelector("#thread-title")?.textContent === expected, title);
    await page.getByText("最後有效對話", {exact:true}).waitFor();
    assert.equal(stateReads, before + 1);
    assert.equal(sends, 1);
    staleState(); pendingSend();
    await page.waitForFunction(() => document.getElementById("message").value === "");
    assert.equal(await page.getByText("過時工作", {exact:true}).count(), 0);
    assert.equal(sends, 1);

    await context.setOffline(true);
    await page.locator("#connection-recovery").waitFor({state:"visible"});
    await page.getByText("最後有效對話", {exact:true}).waitFor();
    assert.equal(await page.locator("#send").isDisabled(), true);
    await page.reload();
    await page.getByText("暫時無法開啟，正在重新連接服務…", {exact:true}).waitFor();
    assert.equal(await page.getByText("最後有效對話", {exact:true}).count(), 0);
    assert.deepEqual(await page.evaluate(() => caches.keys()), []);
    await context.setOffline(false);
    await page.waitForFunction(expected => [...document.querySelectorAll("#thread-title, #sessions .session-name")].some(node => node.textContent === expected), title);
    assert.equal(await page.locator("#connection-recovery").isVisible(), false);
    assert.equal(sends, 1);
    assert.deepEqual(errors, []);
    console.log("Chrome 390px: live page recovered from held GET; one refresh and one send; offline cold launch and automatic reload; empty Cache Storage.");
  } finally {
    await browser?.close();
    server.closeAllConnections();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

async function assertUntil(check) {
  for (let retry = 0; retry < 100; retry++) {
    if (check()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.fail("Timed out waiting for the fixture request");
}
