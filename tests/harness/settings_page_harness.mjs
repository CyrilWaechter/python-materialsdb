// Headless check of the settings page general options and colour scheme.
// Runs the real static/settings.js in a Node VM with DOM stubs and asserts:
//   (a) #scheme lists the server schemes and shows the effective scheme from
//       GET /api/config;
//   (b) changing a control posts the matching /api/config patch;
//   (c) apply-colours is enabled with a connected model, calls
//       /api/model/restyle and reports the queued count + listener detail;
//   (d) without a model it is disabled and the hint is visible.
// Usage: node settings_page_harness.mjs <path/to/settings.js>

import fs from "node:fs";
import vm from "node:vm";

// ---- minimal DOM (same stub shape as model_changes_harness.mjs) ----
class El {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attrs = {};
    this.style = {};
    this.dataset = {};
    this._listeners = {};
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.onclick = null;
    this.onchange = null;
    this._html = "";
    this._text = "";
    this.classList = {
      _s: new Set(),
      add(...names) { names.forEach((n) => this._s.add(n)); },
      remove(...names) { names.forEach((n) => this._s.delete(n)); },
      contains(name) { return this._s.has(name); },
    };
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this.children = []; }
  get textContent() { return this._text || this._html; }
  set textContent(v) { this._text = String(v); this._html = String(v); }
  appendChild(child) { this.children.push(child); this._html += child._html ?? ""; }
  insertAdjacentHTML(_loc, html) { this._html += html; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) { for (const fn of this._listeners[type] || []) fn(event); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getElementsByTagName() {
    if (!this._checkbox) this._checkbox = new El("input");
    return [this._checkbox];
  }
  remove() {}
}

const elements = new Map();
const el = (id) => {
  if (!elements.has(id)) elements.set(id, new El(id === "scheme" ? "select" : "div"));
  return elements.get(id);
};

globalThis.document = {
  lang: "en",
  documentElement: { lang: "en" },
  body: new El("body"),
  getElementById: (id) => el(id),
  createElement: (tag) => new El(tag),
  addEventListener() {},
  querySelector: (sel) => el("qs:" + sel),
  querySelectorAll: () => [],
};
globalThis.window = { MATERIALSDB_TOKEN: "test-token" };

// ---- network stub ----
const config = { lang: "fr", country: "CH", scheme: "Lesosai", schemes: ["Lesosai", "Mine"], ignore_producer_color: false };
let changesModelPath = "/m/x.ifc";
let restyleCalls = 0;
const posts = [];
async function fakeFetch(path, options = {}) {
  const json = (data) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  if (path === "/api/config" && (!options.method || options.method === "GET")) return json(config);
  if (path === "/api/config") {
    posts.push(JSON.parse(options.body));
    config.ignore_producer_color = JSON.parse(options.body).ignore_producer_color ?? config.ignore_producer_color;
    return json({ ok: true });
  }
  if (path === "/api/schemes") return json({ schemes: [{ name: "Lesosai", builtin: true }, { name: "Mine", builtin: false }], error: null });
  if (path === "/api/model/changes") return json({ model_path: changesModelPath, counts: {} });
  if (path === "/api/model/restyle") { restyleCalls += 1; return json({ queued: 3, skipped: [], scheme: "Lesosai" }); }
  if (path === "/api/listener/clients") return json({ clients: [{ model_path: "/m/x.ifc", last_status: { status: "applied", detail: "3 material(s) restyled" } }] });
  throw new Error("unhandled fetch: " + path);
}
globalThis.fetch = fakeFetch;
class Response {
  constructor(body, init = {}) {
    this._body = typeof body === "string" ? body : JSON.stringify(body);
    this.headers = new Map(Object.entries(init.headers || {}));
    this.status = init.status || 200;
    this.ok = this.status < 400;
  }
  async json() { return JSON.parse(this._body); }
  blob() { return this._body; }
}

// ---- load the real frontend ----
const src = fs.readFileSync(process.argv[2], "utf-8");
const expose =
  "\n;Object.assign(globalThis,{" +
  "__get:(id)=>document.getElementById(id)," +
  "__loadSettings:()=>loadSettings()," +
  "__selectScheme:(name)=>selectScheme(name)," +
  "});";
const sandbox = {
  window: globalThis.window,
  document: globalThis.document,
  fetch: fakeFetch,
  Response,
  console,
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  clearInterval: () => {},
  Math,
  JSON,
  Promise,
  Number,
  String,
  Object,
  Array,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src + expose, sandbox);

const S = sandbox;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const failures = [];
const mark = (name, ok, detail) => {
  if (ok) console.log(`${name}: OK`);
  else {
    failures.push(name);
    console.log(`${name}: FAIL ${detail}`);
  }
};

await sleep(50); // bootstrap: loadSettings()

// (a) scheme options from /api/schemes, effective value from /api/config --
const schemeSelect = S.__get("scheme");
const optionHtml = schemeSelect.innerHTML;
mark(
  "scheme options",
  optionHtml.includes("Lesosai") && optionHtml.includes("Mine") && schemeSelect.value === config.scheme,
  `html=${JSON.stringify(optionHtml)} value=${JSON.stringify(schemeSelect.value)}`,
);

// (b) override checkbox posts {ignore_producer_color: true} ---------------
const ignore = S.__get("ignore-producer-color");
ignore.checked = true;
ignore.dispatch("change");
await sleep(20);
const overridePost = posts[posts.length - 1];
mark(
  "override post",
  overridePost && overridePost.ignore_producer_color === true && Object.keys(overridePost).length === 1,
  JSON.stringify(overridePost),
);

// (b) scheme change posts {scheme: "Mine"} --------------------------------
schemeSelect.value = "Mine";
schemeSelect.dispatch("change");
await sleep(20);
const schemePost = posts[posts.length - 1];
mark("scheme post", schemePost && schemePost.scheme === "Mine", JSON.stringify(schemePost));

// (c) apply enabled with a model, restyle + listener detail ---------------
const applyButton = S.__get("apply-colours");
const hint = S.__get("apply-hint");
const enabled = !applyButton.disabled && hint.style.display === "none";
applyButton.dispatch("click");
await sleep(50);
const statusText = S.__get("apply-status").textContent;
mark(
  "apply",
  enabled && restyleCalls === 1 && /3/.test(statusText) && statusText.includes("3 material(s) restyled"),
  `enabled=${enabled} restyleCalls=${restyleCalls} status=${JSON.stringify(statusText)}`,
);

// (d) no model: apply disabled + hint, without a restyle -------------------
changesModelPath = null;
await S.__loadSettings();
mark(
  "no-model hint",
  applyButton.disabled === true && hint.style.display !== "none" && restyleCalls === 1,
  `disabled=${applyButton.disabled} hint=${JSON.stringify(hint.style.display)} restyleCalls=${restyleCalls}`,
);

if (failures.length) {
  console.log(`SETTINGS FAILED (${failures.length}): ${failures.join(", ")}`);
  process.exit(1);
}
console.log("SETTINGS OK");
