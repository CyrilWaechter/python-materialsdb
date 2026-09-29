const TOKEN = window.MATERIALSDB_TOKEN;
const $ = (id) => document.getElementById(id);
const selected = new Set();
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

const USAGE_LABELS = {
  en: { wall: "Wall", roof: "Roof", floor: "Floor", door: "Door" },
  fr: { wall: "Mur", roof: "Toit", floor: "Plancher", door: "Porte" },
  de: { wall: "Wand", roof: "Dach", floor: "Boden", door: "T\u00fcr" },
};

const isEmbed = new URLSearchParams(location.search).has("embed");
let allMaterials = [];
let lang = "en";
if (isEmbed) {
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelector(".top-tabs")?.remove();
    const sp = document.getElementById("settings-panel");
    if (sp) sp.remove();
  });
}
let sortKey = "company";
let sortAsc = true;
const facetSelections = { company: new Set(), category: new Set(), type: new Set(), usage: new Set() };
const detailCache = new Map();
const expanded = new Set();
const layerSelections = new Map();   // materialId -> Set(sourceLayerGuid); absent = whole material
let lastSelectedId = null;
// `/api/model/changes` payload + per-material update/replace choices.
let modelChanges = null;
const modelChoices = new Map();   // materialId -> {mode, candidates, update, replaces}
let pendingReplace = null;        // changed material awaiting a replacement pick
let openReportId = null;          // material whose change report is currently shown
if (isEmbed) {
  const _buildItems = () => {
    const items = [];
    const seen = new Set();
    for (const id of selected) {
      seen.add(id);
      const layerSet = layerSelections.get(id);
      if (layerSet && layerSet.size) {
        for (const lguid of layerSet) {
          const det = detailCache.get(id);
          const layerDetail = det?.layers?.find((l) => l.id === lguid);
          const thick = layerDetail ? Number(layerDetail.thick) : 0;
          items.push({ material_id: id, thickness_m: thick ? thick / 1000 : 0.2 });
        }
      } else {
        items.push({ material_id: id });
      }
    }
    for (const [id, layerSet] of layerSelections) {
      if (seen.has(id) || !layerSet.size) continue;
      for (const lguid of layerSet) {
        const det = detailCache.get(id);
        const layerDetail = det?.layers?.find((l) => l.id === lguid);
        const thick = layerDetail ? Number(layerDetail.thick) : 0;
        items.push({ material_id: id, thickness_m: thick ? thick / 1000 : 0.2 });
      }
    }
    return items;
  };
  const _notifyParent = () => {
    try { parent.postMessage({ type: "picker-selection", items: _buildItems() }, "*"); } catch {}
  };
  const _origAdd = selected.add.bind(selected); selected.add = (v) => { const r = _origAdd(v); _notifyParent(); return r; };
  const _origDelete = selected.delete.bind(selected); selected.delete = (v) => { const r = _origDelete(v); _notifyParent(); return r; };
  const _origClear = selected.clear.bind(selected); selected.clear = () => { const r = _origClear(); _notifyParent(); return r; };
  const _origLSet = layerSelections.set.bind(layerSelections); layerSelections.set = (k, v) => { const r = _origLSet(k, v); _notifyParent(); return r; };
  const _origLDel = layerSelections.delete.bind(layerSelections); layerSelections.delete = (k) => { const r = _origLDel(k); _notifyParent(); return r; };
  const _origLClear = layerSelections.clear.bind(layerSelections); layerSelections.clear = () => { const r = _origLClear(); _notifyParent(); return r; };
}
const COLUMNS = [
  { key: "display_name", label: "name" },
  { key: "company", label: "company", facet: true },
  { key: "category", label: "category", facet: true },
  { key: "type", label: "type", facet: true },
  { key: "lambda", label: "\u03bb W/mK" },
  { key: "thick", label: "thick mm" },
  { key: "usage", label: "usage", facet: true },
];

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-MaterialsDB-Token": TOKEN },
  });
  const type = response.headers.get("Content-Type") || "";
  if (!response.ok) {
    const message = type.includes("json") ? (await response.json()).error : response.statusText;
    throw new Error(message);
  }
  return type.includes("json") ? response.json() : response.blob();
}

function fmt(range, digits = 3) {
  if (range === null || range === undefined) return "";
  return Array.isArray(range)
    ? `${Number(range[0]).toFixed(digits)} - ${Number(range[1]).toFixed(digits)}`
    : Number(range).toFixed(digits);
}

function usageWords(m) {
  const labels = USAGE_LABELS[lang] || USAGE_LABELS.en;
  return Object.entries(m.usage).filter(([, v]) => v).map(([k]) => labels[k] || k);
}

async function getDetail(id) {
  if (!detailCache.has(id)) detailCache.set(id, await api(`/api/materials/${id}`));
  return detailCache.get(id);
}

async function loadMaterials() {
  try {
    const cfg = await api("/api/config");
    lang = cfg.lang || lang;
    const langEl = document.getElementById("lang");
    if (langEl) langEl.value = lang;
    const countryEl = document.getElementById("country");
    if (countryEl && cfg.country) countryEl.value = cfg.country;
    document.documentElement.lang = lang;
  } catch {}
  const params = new URLSearchParams();
  params.set("lang", lang);
  const { materials } = await api(`/api/materials?${params}`);
  const hint = document.getElementById("hint");
  if (hint) {
    hint.style.display = materials.length ? "none" : "block";
    hint.textContent = "No materials cached yet — click Refresh to download from materialsdb.org";
  }
  allMaterials = materials;
  renderHeader();
  applyModel();
}

function facetValues(key) {
  if (key === "usage") return Object.keys(USAGE_LABELS.en);
  return [...new Set(allMaterials.map((m) => String(m[key] ?? "")))].sort();
}

function numericComparator(key, dir) {
  const numeric = (m) => m[key === "lambda" ? "lambda_min" : "thick_min"];
  return (a, b) => {
    const va = numeric(a);
    const vb = numeric(b);
    const aNull = va === null || va === undefined;
    const bNull = vb === null || vb === undefined;
    if (aNull || bNull) return aNull && bNull ? 0 : (aNull ? 1 : -1);   // null metrics stay last
    return dir * (va - vb);
  };
}

function renderHeader() {
  const headerRow = $("header-row");
  headerRow.innerHTML = `<th></th>` + COLUMNS.map((col) => {
    const arrow = sortKey === col.key ? (sortAsc ? " \u2191" : " \u2193") : "";
    const chevron = col.facet ? ` <span class="chevron" data-facet="${col.key}">\u25be</span>` : "";
    return `<th data-sort="${col.key}" style="cursor:pointer">${esc(col.label)}${arrow}${chevron}</th>`;
  }).join("");
  headerRow.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", (event) => {
      if (event.target.classList.contains("chevron")) return;
      const key = th.dataset.sort;
      const numericCol = key === "lambda" || key === "thick";
      if (sortKey === key && numericCol) {
        sortAsc = !sortAsc;
        allMaterials.sort(numericComparator(key, sortAsc ? 1 : -1));   // sign factor: nulls stay last
      } else if (sortKey === key) { sortAsc = !sortAsc; allMaterials.reverse(); }
      else {
        sortKey = key;
        sortAsc = true;
        allMaterials.sort(numericCol ? numericComparator(key, 1)
          : (a, b) => String(a[key] ?? "").localeCompare(String(b[key] ?? "")));
      }
      renderHeader();
      applyModel();
    });
  });
  headerRow.querySelectorAll(".chevron").forEach((el) => {
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleFacetDropdown(el.dataset.facet, el);
    });
  });
}

function matchesFacets(m) {
  const usageActive = (k) => m.usage[k];
  for (const [facet, chosen] of Object.entries(facetSelections)) {
    if (!chosen.size) continue;
    if (facet === "usage") {
      if (![...chosen].some((k) => usageActive(k))) return false;
    } else if (!chosen.has(String(m[facet] ?? ""))) {
      return false;
    }
  }
  return true;
}

let renderGeneration = 0;

function modelBadgeHtml(m) {
  const status = m.model;
  if (!status || status === "absent") return "";
  return `<span class="model-badge" data-status="${esc(status)}">${esc(MODEL_BADGES[status] || status)}</span>`;
}

function modelReportActionHtml(m) {
  if (m.model !== "changed") return "";
  return `<span class="model-actions"><button data-model-action="changes" data-id="${esc(m.id)}">review changes</button></span>`;
}

async function applyModel() {
  const generation = ++renderGeneration;
  const needle = $("text").value.trim().toLowerCase();
  const rowsEl = $("rows");
  const matching = [];
  for (const m of allMaterials) {
    if (!matchesFacets(m)) continue;
    if (needle && !(m.display_name.toLowerCase().includes(needle) ||
        m.company.toLowerCase().includes(needle) || m.category.toLowerCase().includes(needle))) continue;
    matching.push(m);
  }
  let details;
  try {
    details = new Map(await Promise.all([...expanded].map(async (id) => [id, await getDetail(id)])));
  } catch (err) {
    if (generation !== renderGeneration) return;
    setStatus(`detail load failed: ${err.message}`);
    return;
  }
  if (generation !== renderGeneration) return; // a newer render superseded this one
  rowsEl.innerHTML = "";
  let visible = 0;
  for (const m of matching) {
    visible += 1;
    const tr = document.createElement("tr");
    tr.dataset.id = m.id;
    if (m.model && m.model !== "absent") tr.dataset.modelStatus = m.model;
    const usage = usageWords(m).map(esc).join(" ");
    const pickCell = m.type === "simple"
      ? `<td><span class="expander" data-id="${esc(m.id)}" style="cursor:pointer">${expanded.has(m.id) ? "\u25be" : "\u25b8"}</span>` +
        `<input type="checkbox" title="checked = pick whole material (all layers); untick to choose layers below"></td>`
      : `<td><input type="checkbox" title="pick whole material"></td>`;
    tr.innerHTML = pickCell +
      `<td>${esc(m.display_name)} ${modelBadgeHtml(m)}${modelReportActionHtml(m)}</td>` +
      `<td>${esc(m.company)}</td><td>${esc(m.category)}</td><td>${esc(m.type)}</td>` +
      `<td>${esc(fmt([m.lambda_min, m.lambda_max]))}</td><td>${esc(fmt([m.thick_min, m.thick_max], 0))}</td>` +
      `<td>${usage}</td>`;
    const [checkbox] = tr.getElementsByTagName("input");
    checkbox.onchange = () => {
      if (checkbox.checked) {
        layerSelections.delete(m.id);   // parent checked = all layers
        if (pendingReplace && pendingReplace !== m.id) applyReplace(pendingReplace, m.id);
      }
      checkbox.checked ? selected.add(m.id) : selected.delete(m.id);
      document.querySelectorAll(`tr.layerrow[data-parent="${m.id}"] input[data-layer]`)
        .forEach((el) => { el.checked = false; });
    };
    const expander = tr.querySelector(".expander");
    if (expander) expander.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleExpand(tr, m);
    });
    tr.addEventListener("click", (event) => {
      if (event.target.tagName === "INPUT") return;
      if (event.target.closest && event.target.closest("[data-model-action]")) return;
      if (pendingReplace && pendingReplace !== m.id) {
        // clicking any other row IS the replacement choice; when this row is
        // already checked the change event will not fire, so apply directly
        if (!checkbox.checked) {
          checkbox.checked = true;
          checkbox.onchange();
        } else {
          applyReplace(pendingReplace, m.id);
        }
      }
      document.querySelectorAll("tr.selected").forEach((el) => el.classList.remove("selected"));
      tr.classList.add("selected");
      showDetail(m.id);
    });
    rowsEl.appendChild(tr);
    if (expanded.has(m.id) && details.has(m.id)) {
      tr.insertAdjacentHTML("afterend", layerRowsHtml(m, details.get(m.id)));
    }
  }
  if (!visible) rowsEl.innerHTML = `<tr><td colspan="8" style="color:#888">no materials match</td></tr>`;
  rowsEl.querySelectorAll("input[data-layer]").forEach((input) => bindLayerCheckbox(input));
  setStatus(`${visible} materials`);
}

function toggleExpand(tr, m) {
  if (expanded.has(m.id)) { expanded.delete(m.id); }
  else { expanded.add(m.id); }
  applyModel();   // rerender includes child rows below
}

function layerRowsHtml(m, detail) {
  return detail.layers.map((layer) => {
    const chosen = (layerSelections.get(m.id) || new Set()).has(layer.id);
    return `<tr class="layerrow" data-parent="${esc(m.id)}">` +
      `<td><input type="checkbox" data-layer="${esc(layer.id)}" data-material="${esc(m.id)}"` +
      ` title="pick only this layer"${chosen ? " checked" : ""}></td>` +
      `<td colspan="7">\u251c ${esc(String(layer.id).slice(0, 8))}\u2026 · ${fmt(layer.thick, 0)} mm · \u03bb ${esc(fmt(layer.lambda_value))}</td></tr>`;
  }).join("");
}

function bindLayerCheckbox(input) {
  input.addEventListener("change", () => {
    const materialId = input.dataset.material;
    const set = layerSelections.get(materialId) || new Set();
    input.checked ? set.add(input.dataset.layer) : set.delete(input.dataset.layer);
    layerSelections.set(materialId, set);
  });
}

function toggleFacetDropdown(facetKey, anchor) {
  const existing = document.querySelector(".facet");
  if (existing) { existing.remove(); return; }
  const box = document.createElement("div");
  box.className = "facet";
  const values = facetValues(facetKey).map((value) => {
    const count = allMaterials.filter((m) =>
      facetKey === "usage" ? m.usage[value] : String(m[facetKey] ?? "") === value).length;
    const checked = facetSelections[facetKey].has(value);
    const label = facetKey === "usage" ? ((USAGE_LABELS[lang] || USAGE_LABELS.en)[value] || value) : value;
    return `<label><input type="checkbox" data-value="${esc(value)}"${checked ? " checked" : ""}> ${esc(label)} (${count})</label>`;
  }).join("");
  box.innerHTML = `<div class="label">${esc(facetKey)}</div>${values}` +
    `<div style="margin-top:.3rem"><button class="mock-button" data-clear>clear</button></div>`;
  anchor.parentElement.appendChild(box);
  box.addEventListener("change", (event) => {
    const input = event.target;
    if (input.dataset.value === undefined) return;
    input.checked ? facetSelections[facetKey].add(input.dataset.value)
                  : facetSelections[facetKey].delete(input.dataset.value);
    applyModel();
  });
  box.querySelector("[data-clear]").onclick = () => { facetSelections[facetKey].clear(); box.remove(); applyModel(); };
}

document.addEventListener("click", (event) => {
  if (!event.target.closest(".facet") && !event.target.classList.contains("chevron")) {
    document.querySelector(".facet")?.remove();
  }
});

async function showDetail(id) {
  const m = await api(`/api/materials/${id}`);
  lastSelectedId = id;
  $("preview").style.display = "inline-block";
  const nameLines = Object.entries(m.names)
    .map(([code, value]) => `${esc(code || "(no lang)")}: ${esc(value)}`).join("<br>");
  const description = Object.entries(m.descriptions)
    .map(([code, value]) => `${esc(code || "(no lang)")}: ${esc(value)}`).join("<br>");
  const groups = [
    ["names", nameLines], ["descriptions", description],
    ["company", esc(`${m.company} (${m.company_id})`)], ["category", esc(m.category)], ["type", esc(m.type)],
    ["\u03bb W/mK", fmt([m.lambda_min, m.lambda_max])], ["thickness mm", fmt([m.thick_min, m.thick_max], 0)],
    ["U-value", fmt(m.u_value_without)], ["consref", esc(m.consref)], ["design usage", esc(m.designusage)],
    ["id", esc(m.id)],
  ];
  $("detail").innerHTML = groups.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("");
}

function openDrawer(detail) {
  const m = detail;
  const drawer = $("drawer");
  drawer.style.display = "block";
  let inner;
  if (m.type === "simple" && detail.layers.length) {
    const total = detail.layers.reduce((sum, l) => sum + (l.thick || 0), 0) || 1;
    const bars = detail.layers.map((layer) => {
      const flex = (layer.thick || 0) / total;
      const chosen = (layerSelections.get(m.id) || new Set()).has(layer.id);
      return `<div style="flex:${flex};background:#eef;display:flex;align-items:center;justify-content:center;font-size:.75rem">` +
        `${esc(String(layer.id).slice(0, 8))}\u2026<br>${fmt(layer.thick, 0)} mm<br>` +
        `<input type="checkbox" data-layer="${esc(layer.id)}" data-material="${esc(m.id)}"${chosen ? " checked" : ""}></div>`;
    }).join("");
    inner = `<b>STACK PREVIEW \u2014 ${esc(m.display_name)}</b>` +
      `<div style="float:right"><button id="close-drawer">close</button></div>` +
      `<div class="stackbar">${bars}</div>` +
      `<label style="display:inline"><input type="checkbox" id="all-layers"> pick whole material (all layers)</label>`;
  } else if (m.type === "btk") {
    inner = `<b>VARIATIONS \u2014 ${esc(m.display_name)}</b>` +
      `<div style="float:right"><button id="close-drawer">close</button></div><ul>` +
      detail.variations.map((v) => `<li>${fmt(v.thick, 0)} mm \u00b7 U ${esc(fmt(v.u_value_without))}</li>`).join("") + `</ul>`;
  } else {
    inner = `<b>CONSTRUCTION</b>` +
      `<div style="float:right"><button id="close-drawer">close</button></div>` +
      `<div>consref: ${esc(m.consref || "")} \u00b7 designusage: ${esc(m.designusage || "")}</div>` +
      `<div style="color:#777">assembly composition lives outside this schema record</div>`;
  }
  drawer.innerHTML = inner;
  drawer.querySelector("#close-drawer")?.addEventListener("click", () => (drawer.style.display = "none"));
  drawer.querySelector("#all-layers")?.addEventListener("change", (event) => {
    if (event.target.checked) { layerSelections.delete(m.id); applyModel(); }
  });
  drawer.querySelectorAll("input[data-layer]").forEach((input) => bindLayerCheckbox(input));
}

async function pickIds(action) {
  const items = PickerCore.collectItems(selected, layerSelections);
  if (!items.length) return setStatus("select at least one material or layer");
  if (action === "export") {
    const blob = await api("/api/export", { method: "POST", body: JSON.stringify({ items }) });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "materialsdb_export.ifc"; link.click();
    URL.revokeObjectURL(url);
  } else if (action === "pick") {
    const result = await api("/api/pick", { method: "POST",
      body: JSON.stringify({ items, replace: $("replace").checked }) });
    setStatus(`appended ${result.added}, missing ${result.missing.length}`);
  }
}

async function openSession() {
  const path = prompt("path to existing .ifc to append into:");
  if (!path) return;
  const result = await api("/api/session/open", { method: "POST", body: JSON.stringify({ path }) });
  setStatus(`session open: ${result.path}`);
}

async function saveSession() {
  const path = prompt("save as (blank = original path):");
  const result = await api("/api/session/save", { method: "POST", body: path ? JSON.stringify({ path }) : "{}" });
  setStatus(`saved ${result.saved}`);
}

async function sendToBonsai() {
  const items = sendSelected();
  if (!items.length) return setStatus("select at least one material or layer");
  const client_id = bonsaiTarget.current();
  if (!client_id) return setStatus("no Bonsai listener connected");
  const result = await api("/api/listener/send", {
    method: "POST",
    body: JSON.stringify({ client_id, action: "add_materials", items }),
  });
  PickerCore.flash(setStatus, () => $("status").textContent, `sent to ${bonsaiTarget.label() || "?"}: ${result.summary}`);
}

function setStatus(text) { $("status").textContent = text; return text; }

async function syncUpdatesBanner() {
  const banner = document.getElementById("updates");
  if (!banner) return;
  try {
    const { updates_available } = await api("/api/updates");
    banner.style.display = updates_available ? "inline" : "none";
  } catch {}
}

const MODEL_BADGES = { changed: "changed", current: "up to date", gone: "missing", unknown: "legacy" };
const shortId = (id) => `${String(id ?? "").slice(0, 8)}\u2026`;

function fmtReportValue(value) {
  if (value === null || value === undefined || value === "") return "\u2014";
  return String(value);
}

function changedItem(materialId) {
  return ((modelChanges && modelChanges.changed) || []).find((entry) => entry.material_id === materialId) || null;
}

function renderModelChanges(data) {
  modelChanges = data || { counts: {}, changed: [] };
  const banner = document.getElementById("model-changes");
  if (banner) {
    const counts = modelChanges.counts || {};
    const total = (counts.changed || 0) + (counts.gone || 0) + (counts.unknown || 0);
    if (!modelChanges.model_path || !total) {
      banner.style.display = "none";
      banner.textContent = "";
    } else {
      banner.style.display = "inline";
      banner.textContent =
        `${counts.changed || 0} changed in model \u00b7 ${counts.gone || 0} missing \u00b7 ${counts.unknown || 0} legacy`;
      banner.title = modelChanges.model_path;
    }
  }
  applyModel();
  if (openReportId) openChangeReport(openReportId);
}

async function refreshModelChanges() {
  try {
    renderModelChanges(await api("/api/model/changes"));
  } catch {
    renderModelChanges(null);
  }
}

function candidateOption(candidate, chosenId) {
  const id = typeof candidate === "string" ? candidate : String(candidate.layer_id ?? candidate.id ?? "");
  const thick = typeof candidate === "object" ? (candidate.thick ?? candidate.thickness) : null;
  const label = thick !== null && thick !== undefined ? `${Math.round(Number(thick) * 1000)} mm` : shortId(id);
  const selected = chosenId !== undefined && chosenId !== null && String(chosenId) === id ? " selected" : "";
  return `<option value="${esc(id)}"${selected}>${esc(label)}</option>`;
}

function chooseLayerMapping(materialId) {
  const item = changedItem(materialId);
  const matching = (item && item.matching) || {};
  // Candidate selects only apply to genuinely ambiguous layers.
  if (matching.state !== "ambiguous") return "";
  const candidates = matching.candidates || {};
  const chosen = (modelChoices.get(materialId) || {}).candidates || {};
  const layers = Object.entries(candidates);
  if (!layers.length) return "";
  const selects = layers.map(([modelLayerId, list]) => {
    const options = (list || []).map((candidate) => candidateOption(candidate, chosen[modelLayerId])).join("");
    return `<label class="model-layer-map" style="display:block">${esc(shortId(modelLayerId))} \u2192 ` +
      `<select data-model-layer="${esc(modelLayerId)}" data-id="${esc(materialId)}">${options}</select></label>`;
  }).join("");
  return `<div class="model-layer-mapping">${selects}</div>`;
}

function openChangeReport(materialId) {
  const box = document.getElementById("change-report");
  if (!box) return;
  const item = changedItem(materialId);
  if (!item) {
    openReportId = null;
    box.innerHTML = "";
    box.style.display = "none";
    return;
  }
  openReportId = materialId;
  box.style.display = "block";
  const matching = item.matching || {};
  const choice = modelChoices.get(materialId) || {};
  const reportRows = (item.report || []).map((row) =>
    `<tr><td>${esc(row.field)}</td><td>${esc(fmtReportValue(row.old))}</td><td>\u2192</td>` +
    `<td>${esc(fmtReportValue(row.new))}</td></tr>`).join("");
  let detail;
  if (item.has_snapshot === false) detail = `<div class="change-unavailable">details unavailable</div>`;
  else if (reportRows) detail = `<table class="change-report">${reportRows}</table>`;
  else detail = `<div class="change-report-empty">no field changes recorded</div>`;
  const canUpdate = matching.state === "updatable";
  const canReplace = matching.state === "unmatched" || matching.state === "ambiguous";
  const actions = [];
  if (canUpdate) actions.push(`<button data-model-action="update" data-id="${esc(materialId)}">Update</button>`);
  if (canReplace) actions.push(`<button data-model-action="replace" data-id="${esc(materialId)}">Replace\u2026</button>`);
  actions.push(`<button data-model-action="keep" data-id="${esc(materialId)}">Keep</button>`);
  const chosen = choice.mode ? `<span class="model-choice">chosen: ${esc(choice.mode)}</span>` : "";
  box.innerHTML =
    `<b>changes \u2014 ${esc(shortId(materialId))}</b> <span class="model-state">${esc(matching.state || "unknown")}</span>` +
    detail + chooseLayerMapping(materialId) +
    `<div class="model-actions">${actions.join(" ")} ${chosen}</div>`;
}

function setModelMode(materialId, mode) {
  const choice = modelChoices.get(materialId) || {};
  if (mode === "skip") {
    modelChoices.delete(materialId);
  } else {
    choice.mode = mode;
    if (mode === "update") {
      const item = changedItem(materialId);
      const mapping = (item && item.matching && item.matching.mapping) || {};
      choice.update = { ...mapping, ...(choice.candidates || {}) };
    }
    modelChoices.set(materialId, choice);
  }
  openChangeReport(materialId);
  setStatus(`model change for ${shortId(materialId)}: ${mode}`);
}

function beginReplace(materialId) {
  pendingReplace = materialId;
  setStatus(`pick a replacement material from the list for ${shortId(materialId)}`);
}

function applyReplace(oldId, newId) {
  modelChoices.delete(oldId);
  const choice = modelChoices.get(newId) || {};
  choice.mode = "replace";
  choice.replaces = { material_id: oldId };
  modelChoices.set(newId, choice);
  layerSelections.delete(newId);   // a replacement is always a whole material
  selected.add(newId);             // ...and must travel in the send payload
  pendingReplace = null;
  setStatus(`${shortId(newId)} will replace changed ${shortId(oldId)}`);
}

function sendSelected() {
  /* collectItems union enriched with the per-material model-change choice:
   * `update` carries the resolved layer mapping plus any user-picked candidates
   * (the latest candidate picks win over the mapping captured when Update was
   * chosen). */
  const items = PickerCore.collectItems(selected, layerSelections);
  for (const item of items) {
    const choice = modelChoices.get(item.id);
    if (!choice || !choice.mode || choice.mode === "skip") continue;
    item.mode = choice.mode;
    if (choice.mode === "update") {
      const current = changedItem(item.id);
      const mapping = (current && current.matching && current.matching.mapping) || {};
      item.update = { ...mapping, ...(choice.update || {}), ...(choice.candidates || {}) };
    } else if (choice.mode === "replace" && choice.replaces) {
      item.replaces = choice.replaces;
    }
  }
  return items;
}

$("export").onclick = () => pickIds("export").catch((err) => setStatus(err.message));
$("pick").onclick = () => pickIds("pick").catch((err) => setStatus(err.message));
$("open").onclick = () => openSession().catch((err) => setStatus(err.message));
$("save").onclick = () => saveSession().catch((err) => setStatus(err.message));
$("refresh").onclick = runRefresh;

$("rows").addEventListener("click", (event) => {
  const button = event.target.closest && event.target.closest("[data-model-action]");
  if (!button || button.dataset.modelAction !== "changes") return;
  event.stopPropagation();
  openChangeReport(button.dataset.id);
});

$("change-report").addEventListener("click", (event) => {
  const button = event.target.closest && event.target.closest("[data-model-action]");
  if (!button) return;
  const materialId = button.dataset.id;
  if (!materialId) return;
  if (button.dataset.modelAction === "update") setModelMode(materialId, "update");
  else if (button.dataset.modelAction === "replace") beginReplace(materialId);
  else if (button.dataset.modelAction === "keep") setModelMode(materialId, "skip");
});

$("change-report").addEventListener("change", (event) => {
  const select = event.target;
  const modelLayerId = select && select.dataset && select.dataset.modelLayer;
  const materialId = select && select.dataset && select.dataset.id;
  if (!modelLayerId || !materialId) return;
  const choice = modelChoices.get(materialId) || {};
  choice.candidates = { ...(choice.candidates || {}), [modelLayerId]: select.value };
  modelChoices.set(materialId, choice);
});

async function runRefresh() {
  const existing = document.getElementById("refresh-overlay");
  if (existing) return;
  const overlay = document.createElement("div");
  overlay.id = "refresh-overlay";
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:1000";
  overlay.innerHTML = `<div style="background:#fff;border-radius:6px;padding:1rem 1.2rem;min-width:22rem;display:flex;flex-direction:column;gap:.6rem">` +
    `<b>refreshing materialsdb cache</b>` +
    `<div style="height:.6rem;background:#eee;border-radius:.3rem;overflow:hidden"><div id="refresh-bar" style="height:100%;width:0;background:#468;border-radius:.3rem;transition:width .3s"></div></div>` +
    `<div style="display:flex;justify-content:space-between;align-items:center"><span id="refresh-label" style="color:#666">starting…</span>` +
    `<button id="refresh-cancel">cancel</button></div></div>`;
  document.body.appendChild(overlay);
  overlay.querySelector("#refresh-cancel").onclick = () =>
    api("/api/refresh/cancel", { method: "POST", body: "{}" }).catch(() => {});
  try {
    await api("/api/refresh", { method: "POST", body: "{}" });
  } catch (err) {
    overlay.remove();
    setStatus(err.message);
    return;
  }
  let job = null;
  for (let i = 0; i < 200; i++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    try {
      job = await api("/api/refresh/status");
    } catch {
      overlay.remove();
      setStatus("server unreachable while refreshing");
      return;
    }
    if (job.status !== "running" && job.status !== "idle") break;
    const bar = overlay.querySelector("#refresh-bar");
    bar.style.width = job.total ? `${Math.max(4, Math.round((job.done / job.total) * 100))}%` : "4%";
    overlay.querySelector("#refresh-label").textContent = job.label || "…";
  }
  overlay.remove();
  if (job && job.status === "cancelled") {
    setStatus(`refresh cancelled (${job.done}/${job.total || "?"} downloaded)`);
  } else if (job && job.status === "error") {
    setStatus(job.error || "refresh failed");
  } else if (job && job.report) {
    const r = job.report;
    setStatus(`cache refreshed: ${r.downloaded} downloaded, ${r.existing} unchanged, ${r.updated.length} indexed`);
  } else {
    setStatus("refresh still running in background — reopening it will show its progress");
  }
  await loadMaterials();
  await syncUpdatesBanner();
  await refreshModelChanges();
}
$("send-bonsai").onclick = () => sendToBonsai().catch((err) => setStatus(err.message));
const bonsaiTarget = PickerCore.startTargetPoll(api, $("bonsai-target"), $("send-bonsai"));

let debounceTimer;
$("text").addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(applyModel, 250);
});

$("lang").addEventListener("change", async () => {
  lang = $("lang").value;
  await api("/api/config", { method: "POST", body: JSON.stringify({ lang }) });
  detailCache.clear();
  await loadMaterials();
  await syncUpdatesBanner();
  await refreshModelChanges();
});

$("country").addEventListener("change", async () => {
  await api("/api/config", { method: "POST", body: JSON.stringify({ country: $("country").value }) });
  detailCache.clear();
  await loadMaterials();
  await syncUpdatesBanner();
  await refreshModelChanges();
});

$("preview").onclick = async () => {
  if (!lastSelectedId) return setStatus("select a material first");
  openDrawer(await getDetail(lastSelectedId));
};

document.getElementById("settings-tab").addEventListener("click", (e) => {
  e.preventDefault();
  const p = document.getElementById("settings-panel");
  p.style.display = p.style.display === "none" ? "block" : "none";
});

loadMaterials();
syncUpdatesBanner();
refreshModelChanges();
