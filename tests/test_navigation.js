"use strict";
// Exercise the app's navigation against a browser-history stand-in. Rendering and
// HTTP are stubbed; history entries, click handlers, drafts and scroll state are real app logic.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");

function browser(saved, {search = "", hash = ""} = {}) {
  const nodes = new Map(), listeners = new Map();
  const frames = [];
  const makeNode = () => ({value: "", hidden: false, disabled: false, handlers: {}, children: [], dataset: {},
    scrollTop: 0, scrollHeight: 3000, clientHeight: 600, clientTop: 0, layoutTop: 1000, height: 80,
    getBoundingClientRect() { return {top: this.layoutTop - document.scrollingElement.scrollTop, height: this.height}; },
    append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
    querySelector() { return {getBoundingClientRect: () => ({height: 100})}; },
    addEventListener(name, callback) { this.handlers[name] = callback; }});
  const document = {
    scrollingElement: {scrollTop: 0, scrollHeight: 3000, clientHeight: 600}, addEventListener(name, callback) { listeners.set(name, callback); },
    createElement(tag) { return {...makeNode(), tagName: tag.toUpperCase()}; },
    createTextNode(text) { return {textContent: text}; },
    getElementById(id) {
      if (!nodes.has(id)) {
        const classes = new Set();
        nodes.set(id, {...makeNode(),
          classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
            toggle(name, value) { if (value ?? !classes.has(name)) classes.add(name); else classes.delete(name); }},
          addEventListener(name, callback) { this.handlers[name] = callback; }});
      }
      return nodes.get(id);
    }
  };
  const navigation = saved || {entries: [null], index: 0};
  const history = {
    get state() { return navigation.entries[navigation.index]; },
    get length() { return navigation.entries.length; },
    replaceState(state) { navigation.entries[navigation.index] = structuredClone(state); },
    pushState(state) { navigation.entries.splice(++navigation.index, Infinity, structuredClone(state)); },
    back() { this.go(-1); }, forward() { this.go(1); },
    go(delta) {
      const target = navigation.index + delta;
      if (target < 0 || target >= navigation.entries.length) return;
      queueMicrotask(() => { navigation.index = target; listeners.get("popstate")(); });
    }
  };
  const context = vm.createContext({document, history, URLSearchParams,
    location: {hash, pathname: "/", search}, sessionStorage: {getItem() { return ""; }, setItem() {}, removeItem() {}},
    window: {matchMedia() { return {matches: true}; }, addEventListener(name, callback) { listeners.set(name, callback); },
      scrollTo(x, y) { document.scrollingElement.scrollTop = y; }}, setInterval() {},
    requestAnimationFrame(callback) { frames.push(callback); }});
  const run = code => vm.runInContext(code, context);
  run(fs.readFileSync(path.join(__dirname, "../web/reading.js"), "utf8"));
  run(source);
  run('globalThis.originalLoadThread = loadThread; globalThis.originalRenderHeader = renderHeader; renderSessions = () => {}; renderHeader = () => {}; loadThread = async () => true; connection = () => {};');
  run('sessions = [{id: "a"}, {id: "b"}];');
  return {run, history, navigation, document, events: listeners, node: id => document.getElementById(id),
    frame() { frames.splice(0).forEach(callback => callback()); }};
}
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
module.exports = {browser, flush};

if (require.main === module) (async () => {
  const app = browser();
  app.document.scrollingElement.scrollTop = 420;
  await app.run('selectSession("a")');
  assert.equal(app.history.length, 2);
  assert.equal(app.history.state.pilot.session, "a");
  app.node("message").value = "保留這份草稿";
  app.document.scrollingElement.scrollTop = 1600;
  app.node("back").handlers.click();
  app.node("back").handlers.click(); // A double tap must not leave the app.
  await flush();
  assert.equal(app.navigation.index, 0);
  assert.equal(app.node("workspace").classList.contains("detail"), false);
  assert.equal(app.document.scrollingElement.scrollTop, 420);
  assert.equal(app.run('drafts.get("a")'), "保留這份草稿");

  app.history.forward(); await flush();
  assert.equal(app.node("workspace").classList.contains("detail"), true);
  assert.equal(app.node("message").value, "保留這份草稿");
  assert.equal(app.document.scrollingElement.scrollTop, 1600);
  assert.equal(app.node("back").disabled, false);
  app.history.back(); await flush();
  assert.equal(app.document.scrollingElement.scrollTop, 420);
  await app.run('selectSession("b")');
  await app.run('selectSession("b")');
  assert.equal(app.history.length, 2);
  assert.equal(app.history.state.pilot.session, "b");

  // Reloading a conversation restores it after the first state request, without
  // pushing another entry that would make the next Back appear ineffective.
  const reloaded = browser(structuredClone(app.navigation));
  await reloaded.run('token = "test"; api = async () => ({sessions: [{id:"a"}, {id:"b"}]}); poll();');
  assert.equal(reloaded.run("selected"), "b");
  assert.equal(reloaded.history.length, 2);
  reloaded.history.back(); await flush();
  assert.equal(reloaded.node("workspace").classList.contains("detail"), false);
  assert.equal(reloaded.document.scrollingElement.scrollTop, 420);

  const closed = browser(structuredClone(app.navigation));
  await closed.run('token = "test"; api = async () => ({sessions: []}); poll();');
  assert.equal(closed.history.state.pilot.view, "list");
  assert.equal(closed.node("workspace").classList.contains("detail"), false);
  console.log("Navigation checks passed: visible back action, double tap, browser back/forward, drafts, scroll, reload and closed sessions.");
})().catch(error => { console.error(error); process.exitCode = 1; });
