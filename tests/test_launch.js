"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
function browser() {
  const nodes = new Map(), calls = [];
  function node() {
    return {value: "", hidden: false, disabled: false, checked: false, textContent: "", children: [], listeners: {},
      classList: {toggle() {}}, setAttribute() {}, append(...items) { this.children.push(...items); },
      replaceChildren(...items) { this.children = items; }, addEventListener(name, fn) { this.listeners[name] = fn; },
      fire(name) { return this.listeners[name]?.({preventDefault() {}}); }};
  }
  const $ = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  let count = 0;
  const context = vm.createContext({$, token: "paired", window: {scrollTo() {}}, mobileLayout: {matches: true},
    requestID: () => `request-${++count}`, poll() {}, Date, TypeError,
    element: (tag, cls, text) => Object.assign(node(), {textContent: text}),
    api: (path, data) => new Promise((resolve, reject) => calls.push({path, data, resolve, reject}))});
  vm.runInContext(fs.readFileSync("web/launch.js", "utf8"), context);
  return {$, calls, context, launch: context.window.PilotLaunch};
}
async function tests() {
  const b = browser(), {$, calls, launch} = b;
  launch.update({defaultDirectory: "/project", projects: [
    {id: "claude-project", name: "API", directory: "/api", tool: "claude", toolName: "Claude Code", mode: "specific", sessionID: "old-id", pinned: true},
    {id: "picker", name: "Site", directory: "/site", tool: "codex", toolName: "Codex", mode: "picker"}
  ]});
  $("new-work").fire("click");
  assert.equal($("launch-directory").value, "/project");
  assert.equal($("launch-tool").value, "terminal");
  assert.equal($("launch-agent-options").hidden, true);
  $("launch-tool").value = "claude"; $("launch-tool").fire("change");
  assert.equal($("launch-agent-options").hidden, false);
  $("launch-mode").value = "specific"; $("launch-mode").fire("change");
  assert.equal($("launch-session").required, true);
  await $("launch-form").fire("submit");
  assert.equal(calls.length, 0, "Empty resume ID must not launch");
  $("launch-session").value = "old-id";
  const submitting = $("launch-form").fire("submit");
  await $("launch-form").fire("submit");
  assert.equal(calls.length, 1, "Double tap launched twice");
  assert.equal($("launch-fields").disabled, true);
  calls[0].resolve({opened: true, binding: "new-window", tool: "claude"}); await submitting;
  assert.equal(launch.takeOpenedWork([{id: "wrong", binding: "new-window", tool: "terminal"}]), "");
  assert.equal(launch.takeOpenedWork([{id: "correct", binding: "new-window", tool: "claude"}]), "correct");
  assert.equal(launch.takeOpenedWork([{id: "correct", binding: "new-window", tool: "claude"}]), "");

  $("tab-projects").fire("click"); $("project-list").children[0].fire("click");
  assert.equal($("launch-mode").value, "specific"); assert.equal($("launch-session").value, "old-id");
  assert.equal($("launch-directory").readOnly, true);
  $("launch-mode").value = "new";
  const saved = $("launch-form").fire("submit");
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].data)), {projectID: "claude-project", mode: "new", sessionID: "", requestID: "request-2"});
  launch.cancelNavigation();
  calls[1].resolve({opened: true, binding: "saved-window", tool: "claude"}); await saved;
  assert.equal(launch.takeOpenedWork([{id: "saved", binding: "saved-window", tool: "claude"}]), "", "Launch stole navigation");
  $("tab-projects").fire("click"); $("project-list").children[1].fire("click");
  assert.equal($("launch-mode").value, "", "Native picker must require a phone-compatible choice");
  await $("launch-form").fire("submit"); assert.equal(calls.length, 2);

  $("new-work").fire("click");
  const first = $("browse-directory").fire("click");
  $("launch-directory").value = "/new";
  const second = $("browse-directory").fire("click");
  calls[3].resolve({directory: "/new", parent: "/", directories: [{name: "child", path: "/new/child"}]}); await second;
  calls[2].resolve({directory: "/old", parent: "/", directories: []}); await first;
  assert.equal($("directory-current").textContent, "/new", "Stale folder request replaced selection");
  $("directory-use").fire("click"); assert.equal($("launch-directory").value, "/new");

  let sending = $("launch-form").fire("submit");
  calls[4].reject(new TypeError("Disconnected")); await sending;
  sending = $("launch-form").fire("submit");
  assert.equal(calls[5].data.requestID, calls[4].data.requestID, "Network retry changed deduplication ID");
  calls[5].resolve({opened: false, uncertain: true}); await sending;
  assert.match($("launch-error").textContent, /無法確認/);
  $("launch-name").value = "Different";
  sending = $("launch-form").fire("submit");
  assert.notEqual(calls[6].data.requestID, calls[5].data.requestID);
  b.context.token = ""; launch.reset();
  calls[6].resolve({opened: true, binding: "late", tool: "terminal"}); await sending;
  assert.equal(launch.takeOpenedWork([{id: "late", binding: "late", tool: "terminal"}]), "");
  assert.equal($("launch-status").textContent, "");
  assert.equal($("launch-fields").disabled, false);
  const folders = browser(), f = folders.$;
  f('new-work').fire('click');
  const listing = f('browse-directory').fire('click');
  assert.equal(f('directory-new').disabled, true);
  folders.calls[0].resolve({directory:'/projects',parent:'/',directories:[]}); await listing;
  f('directory-new').fire('click'); assert.equal(f('directory-create').hidden,false);
  f('directory-name').value = '../escape';
  await f('directory-create-submit').fire('click'); assert.equal(folders.calls.length,1);
  f('directory-name').value = '新專案';
  let creation = f('directory-create-submit').fire('click');
  await f('directory-create-submit').fire('click'); assert.equal(folders.calls.length,2,'Double tap creates once');
  assert.equal(folders.calls[1].path,'/api/directories');
  assert.equal(folders.calls[1].data.parent,'/projects');
  folders.calls[1].reject(new TypeError('Disconnected')); await creation;
  assert.equal(f('directory-name').value,'新專案');
  creation = f('directory-create-submit').fire('click');
  assert.equal(folders.calls[2].data.requestID,folders.calls[1].data.requestID,'Creation retry keeps the same ID');
  folders.calls[2].resolve({created:true,directory:'/projects/新專案'}); await creation;
  assert.equal(f('launch-directory').value,'/projects/新專案');
  assert.equal(f('launch-name').value,'新專案');
  assert.equal(f('directory-browser').hidden,true);
  assert.ok(!folders.calls.some(c=>c.path==='/api/launch'),'Creating a folder must not start an agent');
  const nextListing=f('browse-directory').fire('click');
  folders.calls.at(-1).resolve({directory:'/projects/新專案',parent:'/projects',directories:[]}); await nextListing;
  f('directory-name').value='another';
  creation=f('directory-create-submit').fire('click');
  folders.launch.cancelNavigation();
  folders.calls.at(-1).resolve({created:true,directory:'/projects/新專案/another'}); await creation;
  assert.equal(f('launch-directory').value,'/projects/新專案','Late creation cannot change a draft after navigation');
  console.log("Mobile launch checks passed");
}
tests().catch(error => { console.error(error); process.exitCode = 1; });
