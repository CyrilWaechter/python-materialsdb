// Headless check of the picker's model-change status, report and update flow.
// Runs the real static/picker-core.js + static/app.js in a Node VM with DOM
// stubs and asserts:
//   (a) a `changed` material renders the banner count and its report rows;
//   (b) choosing Update puts mode:"update" + the mapping into the send payload.
// Usage: node model_changes_harness.mjs <path/to/picker-core.js> <path/to/app.js>

import fs from "node:fs";
import vm from "node:vm";

const M1 = "00000000-0000-0000-0000-000000000001";
const MODEL_LAYER = "00000000-0000-0000-0000-0000000000a1";
const NEW_LAYER = "00000000-0000-0000-0000-0000000000b1";

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
  if (!elements.has(id)) elements.set(id, new El(id === "rows" ? "tbody" : "div"));
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
globalThis.location = { search: "" };
globalThis.URLSearchParams = URLSearchParams;

// ---- network stub ----
const sentPayloads = [];
const changesData = {
  model_path: "/m/wall.ifc",
  seen_at: 123,
  counts: { changed: 1, gone: 0, unknown: 0 },
  changed: [
    {
      material_id: M1,
      matching: { state: "updatable", mapping: { [MODEL_LAYER]: NEW_LAYER }, candidates: {} },
      report: [
        { field: "name", old: "Old name", new: "Isolant A" },
        { field: "lambda", old: 0.04, new: 0.036 },
      ],
      has_snapshot: true,
    },
  ],
  gone: [],
  unknown: [],
};

async function fakeFetch(path, options = {}) {
  const jsonHeaders = { "Content-Type": "application/json" };
  const json = (data) => new Response(JSON.stringify(data), { headers: jsonHeaders });
  if (path === "/api/config") return json({ lang: "en", country: "CH" });
  if (path.startsWith("/api/materials?")) {
    return json({
      materials: [
        {
          id: M1,
          display_name: "Isolant A",
          company: "Acme",
          category: "Insulation",
          type: "simple",
          lambda_min: 0.036,
          lambda_max: 0.036,
          thick_min: 200,
          thick_max: 200,
          usage: { wall: true },
          model: "changed",
        },
      ],
    });
  }
  if (path === "/api/model/changes") return json(changesData);
  if (path === "/api/updates") return json({ updates_available: false });
  if (path === "/api/listener/clients") {
    return json({ clients: [{ client_id: "c1", model_path: "/m/wall.ifc", last_status: { status: "applied" } }] });
  }
  if (path === "/api/listener/send") {
    sentPayloads.push(JSON.parse(options.body));
    return json({ ok: true, queued: 1, summary: "1 material(s)", missing: [], warnings: [] });
  }
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
const coreSrc = fs.readFileSync(process.argv[2], "utf-8");
const appSrc = fs.readFileSync(process.argv[3], "utf-8");
const expose =
  "\n;Object.assign(globalThis,{" +
  "__get:(id)=>document.getElementById(id)," +
  "__renderModelChanges:(d)=>renderModelChanges(d)," +
  "__openChangeReport:(id)=>openChangeReport(id)," +
  "__chooseLayerMapping:(id)=>chooseLayerMapping(id)," +
  "__setModelMode:(id,mode)=>setModelMode(id,mode)," +
  "__sendSelected:()=>sendSelected()," +
  "__modelChoices:()=>modelChoices," +
  "__sent:()=>globalThis.__sentPayloads," +
  "});";
const sandbox = {
  window: globalThis.window,
  document: globalThis.document,
  location: globalThis.location,
  fetch: fakeFetch,
  Response,
  URLSearchParams,
  URL,
  console,
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  clearInterval: () => {},
  Math, JSON, Promise, Number, String, Object, Array,
};
sandbox.globalThis = sandbox;
sandbox.__sentPayloads = sentPayloads;
vm.createContext(sandbox);
vm.runInContext(coreSrc + "\n" + appSrc + expose, sandbox);

const S = sandbox;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const failures = [];
const check = (name, ok, detail) => {
  if (ok) console.log(`OK ${name}`);
  else {
    failures.push(name);
    console.log(`FAIL ${name}: ${detail}`);
  }
};

await sleep(80); // bootstrap: loadMaterials() + refreshModelChanges()

// (a) banner count ------------------------------------------------
const banner = S.__get("model-changes");
const bannerText = banner.textContent || banner.innerHTML;
check(
  "banner shows the changed count",
  banner.style.display !== "none" && /1 changed in model/.test(bannerText),
  `display=${banner.style.display} text=${JSON.stringify(bannerText)}`,
);

// (a) row status + badge -----------------------------------------
const rows = S.__get("rows");
const tr = rows.children.find((child) => child.dataset.id === M1);
check("changed row carries data-model-status", tr && tr.dataset.modelStatus === "changed", `tr=${tr && tr.dataset.modelStatus}`);
check("changed row shows a visible badge", tr && /model-badge/.test(tr.innerHTML), `html=${tr && tr.innerHTML}`);
check("changed row offers a change report action", tr && /data-model-action="changes"/.test(tr.innerHTML), `html=${tr && tr.innerHTML}`);

// (a) report rows -------------------------------------------------
S.__openChangeReport(M1);
const report = S.__get("change-report");
check(
  "report renders the field rows",
  /name/.test(report.innerHTML) && /Old name/.test(report.innerHTML) && /Isolant A/.test(report.innerHTML),
  `html=${report.innerHTML}`,
);
check("report offers Update for an updatable material", /data-model-action="update"/.test(report.innerHTML), `html=${report.innerHTML}`);

// (b) choose Update -> send payload ------------------------------
const checkbox = tr.getElementsByTagName("input")[0];
checkbox.checked = true;
checkbox.onchange();
const btn = { dataset: { modelAction: "update", id: M1 } };
btn.closest = () => btn;
report.dispatch("click", { target: btn });
check("Update choice is recorded", (S.__modelChoices().get(M1) || {}).mode === "update", JSON.stringify([...S.__modelChoices()]));

const sentItems = S.__sendSelected();
const sentItem = sentItems.find((item) => item.id === M1);
check("send item carries mode:update", sentItem && sentItem.mode === "update", JSON.stringify(sentItem));
check(
  "send item carries the layer mapping",
  sentItem && sentItem.update && sentItem.update[MODEL_LAYER] === NEW_LAYER,
  JSON.stringify(sentItem && sentItem.update),
);

await S.__get("send-bonsai").onclick();
const posted = sentPayloads[sentPayloads.length - 1];
check(
  "POSTed payload carries mode:update + update mapping",
  posted && posted.items[0].mode === "update" && posted.items[0].update[MODEL_LAYER] === NEW_LAYER,
  JSON.stringify(posted),
);

// (a) ambiguous layers + missing snapshot -------------------------
S.__renderModelChanges({
  model_path: "/m/wall.ifc",
  seen_at: 456,
  counts: { changed: 1, gone: 1, unknown: 1 },
  changed: [
    {
      material_id: M1,
      matching: { state: "ambiguous", mapping: {}, candidates: { [MODEL_LAYER]: [NEW_LAYER, "layer-c"] } },
      report: [],
      has_snapshot: false,
    },
  ],
  gone: ["gone-1"],
  unknown: ["unknown-1"],
});
const bannerText2 = banner.textContent || banner.innerHTML;
check(
  "banner shows all three counts",
  /1 changed in model/.test(bannerText2) && /1 missing/.test(bannerText2) && /1 legacy/.test(bannerText2),
  JSON.stringify(bannerText2),
);
S.__openChangeReport(M1);
check(
  "ambiguous layers render a select per model layer",
  /<select[^>]*data-model-layer=/.test(report.innerHTML),
  `html=${report.innerHTML}`,
);
check("Replace is offered for an ambiguous material", /data-model-action="replace"/.test(report.innerHTML), `html=${report.innerHTML}`);
check("Update is not offered for an ambiguous material", !/data-model-action="update"/.test(report.innerHTML), `html=${report.innerHTML}`);
check("missing snapshot shows details unavailable", /details unavailable/.test(report.innerHTML), `html=${report.innerHTML}`);

if (failures.length) {
  console.log(`MODEL-CHANGES FAILED (${failures.length}): ${failures.join(", ")}`);
  process.exit(1);
}
console.log("MODEL-CHANGES OK");
