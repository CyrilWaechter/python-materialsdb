// Headless check of the settings page general options, colour scheme, model
// targeting and the palette editor.
// Runs the real static/settings.js in a Node VM with DOM stubs and asserts:
//   (a) #scheme lists the server schemes and shows the effective scheme from
//       GET /api/config;
//   (b) changing a control posts the matching /api/config patch;
//   (c) #model-target is populated from /api/listener/clients, apply-colours
//       posts its client_id to /api/model/restyle and a stateful clients stub
//       reports pending then applied, so the listener detail lands in
//       #apply-status;
//   (d) without a model the button is disabled, the hint is visible and the
//       model selector is hidden;
//   (e) selecting a built-in loads all 17 editor rows as an editable draft
//       with Save disabled and Save as enabled; editing Concrete re-renders
//       the preview and save-as posts the edited palette under a new name;
//   (f) rename/delete post to the encoded /rename and /delete endpoints and
//       importing an envelope shows the server's 409 conflict before a retry
//       succeeds and selects the imported scheme;
//   (g) export writes the current draft envelope through a stubbed
//       window.showSaveFilePicker, under the name suggested from #scheme-name.
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
  if (!elements.has(id)) elements.set(id, new El(id === "scheme" || id === "model-target" ? "select" : "div"));
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
let restyleBody = null;
let statusPolls = 0; // /api/listener/clients calls since the last restyle
const posts = [];
const schemePosts = [];
const schemeList = [{ name: "Lesosai", builtin: true }, { name: "Mine", builtin: false }];

// Full 17-category palette served for scheme detail GETs (one entry per schema
// category), so the editor's "17 rows" assertion is meaningful.
const CATEGORY_NAMES = [
  "Others", "Water_Proof", "Vapour_Proof", "Concrete", "Wood_Timberproducts",
  "Insulation", "Masonry", "Metal", "Mortar", "Plastics", "Stone", "Composite",
  "Films", "Render", "Covering", "Glas", "Soil",
];
const schemeCategories = Object.fromEntries(CATEGORY_NAMES.map((name, index) => [
  name,
  { hatch: "", color: [(index * 11) % 256, (index * 29) % 256, (index * 47) % 256] },
]));

// stateful listener stub: the first poll after a restyle is still pending, the
// next one reports the applied detail (drives the 1 s post-apply poll)
function listenerClients() {
  if (!changesModelPath) return { clients: [] };
  const pending = restyleCalls > 0 && statusPolls <= 1;
  return {
    clients: [{
      client_id: "c1",
      model_path: changesModelPath,
      last_status: pending
        ? { status: "pending", detail: "" }
        : { status: "applied", detail: "3 material(s) restyled" },
    }],
  };
}

async function fakeFetch(path, options = {}) {
  const json = (data) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  const fail = (status, message) =>
    new Response(JSON.stringify({ error: message }), { status, headers: { "Content-Type": "application/json" } });
  if (path === "/api/config" && (!options.method || options.method === "GET")) return json(config);
  if (path === "/api/config") {
    posts.push(JSON.parse(options.body));
    config.ignore_producer_color = JSON.parse(options.body).ignore_producer_color ?? config.ignore_producer_color;
    return json({ ok: true });
  }
  if (path === "/api/schemes") return json({ schemes: schemeList, error: null });
  // Task 9 routes: must precede the generic POST branch, which would otherwise
  // swallow them as a scheme named "Mine/rename" and so on.
  if (path === "/api/schemes/import" && options.method === "POST") {
    const body = JSON.parse(options.body);
    schemePosts.push({ name: body && body.name, path, body });
    if (body && body.name === "Lesosai") return fail(409, "a scheme named 'Lesosai' already exists");
    if (!schemeList.some((entry) => entry.name === body.name)) schemeList.push({ name: body.name, builtin: false });
    return json({ name: body.name });
  }
  if (path.endsWith("/rename") && options.method === "POST") {
    const name = decodeURIComponent(path.slice("/api/schemes/".length, -"/rename".length));
    const body = JSON.parse(options.body);
    schemePosts.push({ name, path, body });
    const entry = schemeList.find((candidate) => candidate.name === name);
    if (!entry || entry.builtin) return fail(400, `no custom scheme named '${name}'`);
    entry.name = body.new_name;
    if (config.scheme === name) config.scheme = body.new_name;
    return json({ ok: true });
  }
  if (path.endsWith("/delete") && options.method === "POST") {
    const name = decodeURIComponent(path.slice("/api/schemes/".length, -"/delete".length));
    schemePosts.push({ name, path, body: options.body ? JSON.parse(options.body) : {} });
    const index = schemeList.findIndex((candidate) => candidate.name === name && !candidate.builtin);
    if (index < 0) return fail(404, `unknown scheme: ${name}`);
    schemeList.splice(index, 1);
    if (config.scheme === name) config.scheme = "Lesosai";
    return json({ ok: true });
  }
  if (path.startsWith("/api/schemes/") && options.method === "POST") {
    const name = decodeURIComponent(path.slice("/api/schemes/".length));
    const body = JSON.parse(options.body);
    schemePosts.push({ name, path, body });
    if (!schemeList.some((entry) => entry.name === name)) schemeList.push({ name, builtin: false });
    return json({ name, builtin: false, ok: true });
  }
  if (path.startsWith("/api/schemes/") && (!options.method || options.method === "GET")) {
    const name = decodeURIComponent(path.slice("/api/schemes/".length));
    return json({ name, builtin: name === "Lesosai", categories: schemeCategories });
  }
  if (path === "/api/model/changes") return json({ model_path: changesModelPath, counts: {} });
  if (path === "/api/model/restyle") {
    restyleCalls += 1;
    statusPolls = 0;
    restyleBody = JSON.parse(options.body || "{}");
    return json({ queued: 3, skipped: [], scheme: "Lesosai" });
  }
  if (path === "/api/listener/clients") {
    statusPolls += 1;
    return json(listenerClients());
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

// (c) model target: #model-target lists the connected listener's model -----
const modelSelect = S.__get("model-target");
const modelRow = S.__get("model-target-row");
const targetHtml = modelSelect.innerHTML;
mark(
  "model target",
  targetHtml.includes('value="c1"') &&
    targetHtml.includes(">x.ifc<") &&
    targetHtml.includes('title="/m/x.ifc"') &&
    modelSelect.value === "c1" &&
    modelRow.style.display !== "none",
  `html=${JSON.stringify(targetHtml)} value=${JSON.stringify(modelSelect.value)} row=${JSON.stringify(modelRow.style.display)}`,
);

// (c) apply enabled with a model, restyle carries the target client_id, and
// the stateful status poll reports pending then applied --------------------
const applyButton = S.__get("apply-colours");
const hint = S.__get("apply-hint");
const enabled = !applyButton.disabled && hint.style.display === "none";
applyButton.dispatch("click");
await sleep(1200);
const statusText = S.__get("apply-status").textContent;
mark(
  "apply",
  enabled && restyleCalls === 1 && /3 queued/.test(statusText),
  `enabled=${enabled} restyleCalls=${restyleCalls} status=${JSON.stringify(statusText)}`,
);
mark(
  "restyle target",
  JSON.stringify(restyleBody) === JSON.stringify({ client_id: "c1" }),
  `body=${JSON.stringify(restyleBody)}`,
);
mark(
  "apply status poll",
  statusPolls >= 2 && statusText.includes("3 material(s) restyled"),
  `statusPolls=${statusPolls} status=${JSON.stringify(statusText)}`,
);

// (d) no model: apply disabled + hint visible + target selector hidden -----
changesModelPath = null;
await S.__loadSettings();
mark(
  "no-model hint",
  applyButton.disabled === true &&
    hint.style.display !== "none" &&
    modelSelect.style.display === "none" &&
    modelSelect.innerHTML === "" &&
    restyleCalls === 1,
  `disabled=${applyButton.disabled} hint=${JSON.stringify(hint.style.display)} ` +
    `target=${JSON.stringify(modelSelect.style.display)} restyleCalls=${restyleCalls}`,
);

// (e) palette editor: the effective built-in loads as an editable draft with
// Save disabled and Save as enabled -----------------------------------------
const rowsBox = S.__get("palette-rows");
const saveButton = S.__get("save-scheme");
const saveAsButton = S.__get("save-as-scheme");
const nameField = S.__get("scheme-name");
const rowCategories = new Set();
const categoryRe = /data-category="([^"]+)"/g;
let rowMatch;
while ((rowMatch = categoryRe.exec(rowsBox.innerHTML)) !== null) rowCategories.add(rowMatch[1]);
const hexFields = (rowsBox.innerHTML.match(/data-role="hex"/g) || []).length;
const chipsBefore = S.__get("preview-chips").innerHTML;
mark(
  "editor rows",
  rowCategories.size === 17 &&
    hexFields === 17 &&
    saveButton.disabled === true &&
    saveAsButton.disabled === false &&
    nameField.value === "Lesosai copy" &&
    (chipsBefore.match(/class="chip"/g) || []).length === 17,
  `categories=${rowCategories.size} hexFields=${hexFields} save=${saveButton.disabled} ` +
    `saveAs=${saveAsButton.disabled} name=${JSON.stringify(nameField.value)}`,
);

// editing a colour input updates the draft and both live previews ---------
rowsBox.dispatch("input", { target: { dataset: { category: "Concrete" }, value: "#112233" } });
await sleep(10);
const chipsHtml = S.__get("preview-chips").innerHTML;
const sectionHtml = S.__get("preview-section").innerHTML;
const chipCount = (chipsHtml.match(/class="chip"/g) || []).length;
const sectionDivs = (sectionHtml.match(/<div/g) || []).length;
mark(
  "preview",
  chipsHtml.includes("#112233") && chipCount === 17 && sectionHtml.includes("#112233") && sectionDivs === 5,
  `chips=${JSON.stringify(chipsHtml)} section=${JSON.stringify(sectionHtml)}`,
);

// save as… posts the edited built-in draft under the #scheme-name ----------
nameField.value = "Studio Palette";
saveAsButton.dispatch("click");
await sleep(50);
const savedPost = schemePosts[schemePosts.length - 1];
const savedConcrete = savedPost && savedPost.body && savedPost.body.categories && savedPost.body.categories.Concrete;
const categoryCount = savedPost && savedPost.body && savedPost.body.categories ? Object.keys(savedPost.body.categories).length : 0;
const savedColour =
  savedConcrete &&
  savedConcrete.hatch === "" &&
  Array.isArray(savedConcrete.color) &&
  savedConcrete.color.length === 3 &&
  savedConcrete.color[0] === 17 &&
  savedConcrete.color[1] === 34 &&
  savedConcrete.color[2] === 51;
const listRefreshed = S.__get("scheme").innerHTML.includes("Studio Palette");
mark(
  "save payload",
  !!savedPost && savedPost.path === "/api/schemes/Studio%20Palette" && categoryCount === 17 && savedColour && listRefreshed,
  `post=${JSON.stringify(savedPost)} categories=${categoryCount} colour=${JSON.stringify(savedConcrete)} refreshed=${listRefreshed}`,
);

// save-as must not silently overwrite an existing scheme: the conflict is
// inline and nothing is posted ----------------------------------------------
const editorStatusBox = S.__get("editor-status");
const conflictPostsBefore = schemePosts.length;
nameField.value = "Mine";
saveAsButton.dispatch("click");
await sleep(50);
mark(
  "save-as conflict",
  schemePosts.length === conflictPostsBefore && editorStatusBox.textContent.includes("already exists"),
  `posts=${schemePosts.length - conflictPostsBefore} status=${JSON.stringify(editorStatusBox.textContent)}`,
);

// ...while a free name still posts the edited draft under the encoded name ---
nameField.value = "Studio II";
saveAsButton.dispatch("click");
await sleep(50);
const freePost = schemePosts[schemePosts.length - 1];
mark(
  "save-as free name",
  !!freePost &&
    freePost.path === "/api/schemes/Studio%20II" &&
    !!freePost.body &&
    Object.keys(freePost.body.categories).length === 17,
  `post=${JSON.stringify(freePost)}`,
);

// selecting away from a dirty draft asks first: the edited scheme itself is a
// no-op, cancel keeps the draft, accept drops it and switches ---------------
const previewChips = S.__get("preview-chips");
const editedVisible = () => previewChips.innerHTML.includes("#445566");
let confirmCalls = 0;
globalThis.window.confirm = () => {
  confirmCalls += 1;
  return false;
};
rowsBox.dispatch("input", { target: { dataset: { category: "Concrete" }, value: "#445566" } });
await sleep(10);
await S.__selectScheme("Studio II");
mark(
  "select same keeps draft",
  confirmCalls === 0 && editedVisible(),
  `confirmCalls=${confirmCalls} edited=${editedVisible()}`,
);
await S.__selectScheme("Mine");
mark(
  "select cancel keeps draft",
  confirmCalls === 1 &&
    editedVisible() &&
    editorStatusBox.textContent.includes("unsaved") &&
    schemeSelect.value === "Lesosai",
  `confirmCalls=${confirmCalls} edited=${editedVisible()} ` +
    `status=${JSON.stringify(editorStatusBox.textContent)} value=${JSON.stringify(schemeSelect.value)}`,
);
globalThis.window.confirm = () => {
  confirmCalls += 1;
  return true;
};
await S.__selectScheme("Mine");
mark(
  "select accept switches",
  confirmCalls === 2 &&
    schemeSelect.value === "Mine" &&
    !editedVisible() &&
    previewChips.innerHTML.includes("#21578d"),
  `confirmCalls=${confirmCalls} value=${JSON.stringify(schemeSelect.value)} ` +
    `edited=${editedVisible()} switched=${previewChips.innerHTML.includes("#21578d")}`,
);
delete globalThis.window.confirm;

// (g) export: a stubbed showSaveFilePicker receives the current draft --------
rowsBox.dispatch("input", { target: { dataset: { category: "Concrete" }, value: "#aabbcc" } });
await sleep(10);
nameField.value = "Mine Export";
let exportCall = null;
globalThis.window.showSaveFilePicker = async (options) => {
  exportCall = { options, text: null };
  return {
    createWritable: async () => ({
      write: async (text) => { exportCall.text = text; },
      close: async () => {},
    }),
  };
};
S.__get("export-scheme").dispatch("click");
await sleep(50);
let exportEnvelope = null;
try {
  exportEnvelope = exportCall && exportCall.text ? JSON.parse(exportCall.text) : null;
} catch {
  exportEnvelope = null;
}
const exportColour = exportEnvelope && exportEnvelope.categories && exportEnvelope.categories.Concrete;
mark(
  "export",
  !!exportEnvelope &&
    exportEnvelope.format === "materialsdb-scheme/1" &&
    exportEnvelope.name === "Mine Export" &&
    !!exportColour &&
    JSON.stringify(exportColour.color) === JSON.stringify([170, 187, 204]) &&
    exportCall.options.suggestedName === "Mine Export.json",
  `envelope=${JSON.stringify(exportEnvelope)} suggested=${JSON.stringify(exportCall && exportCall.options)}`,
);
delete globalThis.window.showSaveFilePicker;

// rename: posts {"new_name": "Better"} to the encoded /rename endpoint ------
const renameButton = S.__get("rename-scheme");
nameField.value = "Better";
renameButton.dispatch("click");
await sleep(50);
const renamePost = schemePosts.find((entry) => entry.path === "/api/schemes/Mine/rename");
const renameListed = S.__get("scheme").innerHTML.includes("Better");
mark(
  "rename",
  !!renamePost && JSON.stringify(renamePost.body) === JSON.stringify({ new_name: "Better" }) && renameListed,
  `post=${JSON.stringify(renamePost)} listed=${renameListed}`,
);

// put Mine back so the delete exercise starts from a known custom scheme ----
const renamedEntry = schemeList.find((entry) => entry.name === "Better");
if (renamedEntry) renamedEntry.name = "Mine";
config.scheme = "Mine";
await S.__loadSettings();

// delete: posts to the encoded /delete endpoint, then falls back ------------
const deleteButton = S.__get("delete-scheme");
deleteButton.dispatch("click");
await sleep(50);
const deletePost = schemePosts.find((entry) => entry.path === "/api/schemes/Mine/delete");
const selectAfterDelete = S.__get("scheme");
mark(
  "delete",
  !!deletePost && selectAfterDelete.value === "Lesosai" && !selectAfterDelete.innerHTML.includes("Mine"),
  `post=${JSON.stringify(deletePost)} value=${JSON.stringify(selectAfterDelete.value)} html=${JSON.stringify(selectAfterDelete.innerHTML)}`,
);

// import: choosing a file that conflicts with a built-in shows the 409 ------
const importFile = S.__get("import-file");
const importButton = S.__get("import-scheme");
const importLabel = S.__get("import-name-label");
const importName = S.__get("import-name");
const editorStatus = S.__get("editor-status");
const importEnvelope = { format: "materialsdb-scheme/1", name: "Lesosai", categories: schemeCategories };
importFile.files = [{ name: "p.json", text: async () => JSON.stringify(importEnvelope) }];
importFile.dispatch("change");
await sleep(50);
const conflictPost = schemePosts.find((entry) => entry.path === "/api/schemes/import" && entry.body && entry.body.name === "Lesosai");
mark(
  "import conflict",
  !!conflictPost &&
    editorStatus.textContent.includes("already exists") &&
    importLabel.style.display === "" &&
    importName.value === "Lesosai",
  `post=${JSON.stringify(conflictPost)} status=${JSON.stringify(editorStatus.textContent)} ` +
    `label=${JSON.stringify(importLabel.style.display)} name=${JSON.stringify(importName.value)}`,
);

// the still-editable #import-name resolves the conflict on the retry --------
importName.value = "Imported Palette";
importButton.dispatch("click");
await sleep(60);
const importPost = schemePosts.find((entry) => entry.path === "/api/schemes/import" && entry.body && entry.body.name === "Imported Palette");
const selectAfterImport = S.__get("scheme");
mark(
  "import success",
  !!importPost &&
    selectAfterImport.value === "Imported Palette" &&
    selectAfterImport.innerHTML.includes("Imported Palette") &&
    importLabel.style.display === "none",
  `post=${JSON.stringify(importPost)} value=${JSON.stringify(selectAfterImport.value)} ` +
    `html=${JSON.stringify(selectAfterImport.innerHTML)} label=${JSON.stringify(importLabel.style.display)}`,
);

if (failures.length) {
  console.log(`SETTINGS FAILED (${failures.length}): ${failures.join(", ")}`);
  process.exit(1);
}
console.log("SETTINGS OK");
