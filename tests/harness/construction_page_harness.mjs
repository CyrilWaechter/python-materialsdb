import fs from "node:fs";
import vm from "node:vm";

// ---- minimal DOM ----
class El {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attrs = {};
    this.style = {};
    this.dataset = {};
    this._listeners = {};
    this.value = "";
    this.textContent = "";
    this._html = "";
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = v; this.children = []; }
  set textContent(v) { this._html = v; }
  get textContent() { return this._html; }
  appendChild(child) { this.children.push(child); this._html += child.innerHTML ?? ""; }
  insertAdjacentHTML(_loc, html) { this._html += html; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) { for (const fn of this._listeners[type] || []) fn(event); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  remove() {}
}

const elements = new Map();
const el = (id) => {
  if (!elements.has(id)) elements.set(id, new El(id === "layers" ? "tbody" : "div"));
  return elements.get(id);
};

globalThis.document = {
  lang: "en",
  getElementById: (id) => el(id),
  createElement: (tag) => new El(tag),
  body: new El("body"),
  addEventListener() {},
  querySelector(sel) {
    const key = "qs:" + sel;
    if (!elements.has(key)) elements.set(key, new El("div"));
    return elements.get(key);
  },
};
globalThis.window = { MATERIALSDB_TOKEN: "test-token" };
globalThis.document.documentElement = { lang: "en" };

// ---- network stub ----
const DETAIL_002 = { id: "00000000-0000-0000-0000-000000000002", names: { fr: "Beton B" }, layers: [{ id: "b1", thick: 150, lambda_value: 0.21 }] };
const DETAIL_001 = { id: "00000000-0000-0000-0000-000000000001", names: { fr: "Isolant A" }, layers: [{ id: "a1", thick: 200, lambda_value: 0.036 }, { id: "a2", thick: 100, lambda_value: 0.05 }] };

async function fakeFetch(path, options = {}) {
  console.error("[fetch]", path);
  const jsonHeaders = { "Content-Type": "application/json" };
  const json = (data) => new Response(JSON.stringify(data), { headers: jsonHeaders });
  if (path.startsWith("/api/materials?type=construction")) return json({ materials: [] });
  if (path.startsWith("/api/materials/")) {
    const id = decodeURIComponent(path.split("/").pop());
    if (id.endsWith("0002")) return json(DETAIL_002);
    if (id.endsWith("0001")) return json(DETAIL_001);
    return new Response(JSON.stringify({ error: "unknown" }), { status: 404, headers: { "Content-Type": "application/json" } });
  }
  if (path === "/api/u_value") {
    const body = JSON.parse(options.body);
    let rsum = 0;
    const contributions = [];
    const missing = [];
    for (const l of body.construction.layers) {
      const d = l.material_id.endsWith("0002") ? DETAIL_002 : DETAIL_001;
      const lambda = d.layers[0].lambda_value;
      const r = l.thickness_m / lambda;
      rsum += r;
      contributions.push({ material_id: l.material_id, name: d.names.fr, d_m: l.thickness_m, lambda_value: lambda, r });
    }
    return json({ u: 1 / (0.13 + rsum + 0.04), rsi: 0.13, rse: 0.04, contributions, missing_lambda_ids: missing });
  }
  if (path === "/api/constructions") return json({ constructions: [] });
  if (path === "/api/materials?type=construction") return json({ materials: [] });
  throw new Error("unhandled fetch: " + path);
}
globalThis.fetch = fakeFetch;
class Response {
  constructor(body, init = {}) { this._body = typeof body === "string" ? body : JSON.stringify(body); this.headers = new Map(Object.entries(init.headers || {})); this.status = init.status || 200; this.ok = this.status < 400; }
  getheaders() { return Object.fromEntries(this.headers); }
  async json() { return JSON.parse(this._body); }
  blob() { return this._body; }
}
globalThis.Response = Response;

// ---- load the real frontend ----
const src = fs.readFileSync(process.argv[2], "utf-8");
const sandbox = { window: globalThis.window, document: globalThis.document, fetch: fakeFetch, Response,
                  console, setTimeout, clearTimeout, Math, JSON, Promise, Number, String, Object, Array };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src + "\n;Object.assign(globalThis,{__get:(id)=>document.getElementById(id),__addLayer:addLayerFromChooser,__layers:()=>layers,__render:renderLayers,__evalInContext:(code)=>eval(code)});", sandbox);

const S = sandbox;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await sleep(50); // bootstrap loadList()

// instrument renderLayers inside the VM
S.__evalInContext(`window.__calls = [];
  const __origRender = renderLayers;
  renderLayers = function (...args) {
    window.__calls.push({ phase: "in", layersLen: layers.length });
    try { return __origRender.apply(this, args); }
    catch (err) { window.__calls.push({ phase: "throw", message: String(err) }); throw err; }
    finally { const html = document.getElementById("layers").innerHTML;
             window.__calls.push({ phase: "out", dataRows: (html.match(/data-index/g) || []).length }); }
  };`)
console.log("== state after bootstrap ==");
console.log("layers:", JSON.stringify(S.__layers()));

console.log("\n== calling addLayerFromChooser('...0002') ==");
await S.__addLayer("00000000-0000-0000-0000-000000000002");
await sleep(30);
console.log("renderLayers calls:", JSON.stringify(S.window.__calls));
const tbody = S.__get("layers");
console.log("layers array:", JSON.stringify(S.__layers()));
console.log("--- rendered tbody.innerHTML ---");
console.log(tbody.innerHTML);
console.log("--- end ---");
const hasDataRow = tbody.innerHTML.includes('data-index="0"');
const hasName = tbody.innerHTML.includes("Beton B");
console.log(`\ndata row present: ${hasDataRow} | name shown: ${hasName}`);
if (!hasDataRow || !hasName) {
  console.log("BUG REPRODUCED: material rows missing from rendered table");
  process.exit(1);
}
console.log("OK");
