"use strict";
const assert = require("node:assert/strict");
const {browser, flush} = require("./test_navigation.js");

(async () => {
  const page = browser(), reports = [], probes = [];
  let applied = true;
  page.document.documentElement = {};
  page.document.querySelector = () => ({sheet: applied ? {} : null, href: 'https://pilot.example.ts.net/style.css?v=release-hash'});
  // The regular app runs, with just browser APIs and HTTP mocked. Diagnostic
  // metadata must not include paired credentials, URLs or conversation text.
  page.run('token = "paired-secret";');
  const scope = page.run('globalThis');
  scope.getComputedStyle = () => ({getPropertyValue: () => applied ? "#f7f6f2" : ""});
  scope.navigator = {standalone: true}; scope.URL = URL;
  scope.location.origin = "https://pilot.example.ts.net";
  scope.performance = {getEntriesByName: () => [{duration: 20, transferSize: 0, encodedBodySize: 0, decodedBodySize: 0}]};
  scope.setTimeout = () => 1; scope.clearTimeout = () => {};
  scope.AbortController = AbortController; scope.crypto = {};
  scope.fetch = async (url, options) => { probes.push(url); return {status: 503, headers: {get: () => "text/plain"}, arrayBuffer: async () => new ArrayBuffer(12)}; };
  scope.record = (path, data) => { reports.push({path, data: JSON.parse(JSON.stringify(data))}); return {recorded: true}; };
  page.run('api = record;');
  await page.run('reportStyleHealth()');
  await page.run('reportStyleHealth()');
  assert.equal(probes.length, 0);
  assert.equal(reports.length, 1);
  assert.equal(reports[0].data.applied, true);
  applied = false;
  await page.run('reportStyleHealth()'); await flush();
  assert.equal(probes.length, 1);
  assert.equal(probes[0], 'https://pilot.example.ts.net/style.css?v=release-hash');
  assert.equal(reports[1].data.applied, false);
  assert.equal(reports[1].data.sheetLoaded, false);
  assert.equal(reports[1].data.probeStatus, 503);
  assert.equal(reports[1].data.probeBytes, 12);
  assert.equal(JSON.stringify(reports).includes("paired-secret"), false);
  assert.equal(JSON.stringify(reports).includes("https://"), false);
  assert.equal(page.run('selected'), "");
  assert.equal(page.node("message").value, "");
  console.log("Stylesheet diagnostics checks passed: normal load, missing styles, no duplicate report, metadata only and no navigation/draft changes.");
})().catch(error => { console.error(error); process.exitCode = 1; });
