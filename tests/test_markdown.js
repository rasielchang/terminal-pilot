"use strict";
const assert = require("node:assert/strict");
const {browser} = require("./test_navigation.js");
const app = browser(), create = app.document.createElement;
app.document.createElement = tag => {
  const node = create(tag); node.attributes = {};
  node.setAttribute = (key, value) => { node.attributes[key] = value; };
  Object.defineProperty(node, "innerHTML", {set() { throw Error("Model text must not become HTML"); }});
  return node;
};
const render = text => app.run(`renderText(${JSON.stringify(text)})`);
const all = (node, tag) => [...(node.tagName === tag.toUpperCase() ? [node] : []), ...(node.children || []).flatMap(n => all(n, tag))];
const content = node => (node.textContent || "") + (node.children || []).map(content).join("");
const rows = table => all(table, "tr").map(row => row.children.map(content));

const table = all(render("前言\n\n| 項目 | 結果 | 數量 |\n| :--- | :---: | ---: |\n| **手機** | `通過` | 12 |\n| 桌面 | 通過 | 8 |\n\n後記"), "table")[0];
assert.deepEqual(rows(table), [["項目", "結果", "數量"], ["手機", "通過", "12"], ["桌面", "通過", "8"]]);
assert.deepEqual(all(table, "th").map(n => n.className), ["table-left", "table-center", "table-right"]);
assert.ok(all(table, "th").every(n => n.attributes.scope === "col"));
assert.equal(all(table, "strong").length, 1); assert.equal(all(table, "code").length, 1);
const region = render("A | B\n--- | ---\n1 | 2").children[0];
assert.equal(region.tabIndex, 0); assert.equal(region.attributes.role, "region");
assert.match(region.attributes["aria-label"], /左右捲動/);
assert.deepEqual(rows(all(region, "table")[0]), [["A", "B"], ["1", "2"]]);

const escaped = render("| 指令 | 備註 |\r\n| --- | --- |\r\n| `a|b` | x\\|y |\r\n| a\\| | \\|b |\r\n| | |\r\n| 少一格 |\r\n");
assert.deepEqual(rows(all(escaped, "table")[0]), [["指令", "備註"], ["a|b", "x|y"], ["a|", "|b"], ["", ""], ["少一格", ""]]);
const multiple = render("A | B\n--- | ---\n1 | 2\nC | D\n--- | ---\n3 | 4\n# 接著\n- 保留清單");
assert.equal(all(multiple, "table").length, 2);
assert.equal(all(multiple, "h3").length, 1); assert.equal(all(multiple, "ul").length, 1);

// Invalid/unfinished separators and ordinary shell pipes stay readable text.
for (const text of ["a | b\n-- | ---", "a | b\n--- | --- | ---", "a | b\n--- |", "a | b", "cat a | sort\n下一段", "標題\n---"])
  assert.equal(all(render(text), "table").length, 0, text);
assert.equal(all(render("a | b\n--- | ---\n1 | 2"), "table").length, 1);
const extra = render("a | b\n--- | ---\n1 | 2 | 必須保留");
assert.ok(content(extra).includes("1 | 2 | 必須保留"));
const fenced = render("```text\n| A | B |\n| --- | --- |\n| 1 | 2 |\n```");
assert.equal(all(fenced, "table").length, 0); assert.equal(all(fenced, "pre").length, 1);
const hostile = render('| 值 | 說明 |\n| --- | --- |\n| <img src=x onerror=alert(1)> | <script>alert(1)</script> |');
assert.equal(all(hostile, "img").length, 0); assert.equal(all(hostile, "script").length, 0);
assert.ok(content(hostile).includes('<img src=x onerror=alert(1)>'));
console.log("Markdown table checks passed: headers/alignment, safe inline content, escaped/code pipes, partial input, adjacent tables, empty cells and code fences.");
