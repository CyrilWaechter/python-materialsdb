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
if (isEmbed) {
  const _buildItems = () => {
    const items = [];
    for (const id of selected) {
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
    const usage = usageWords(m).map(esc).join(" ");
    const pickCell = m.type === "simple"
      ? `<td><span class="expander" data-id="${esc(m.id)}" style="cursor:pointer">${expanded.has(m.id) ? "\u25be" : "\u25b8"}</span>` +
        `<input type="checkbox" title="checked = pick whole material (all layers); untick to choose layers below"></td>`
      : `<td><input type="checkbox" title="pick whole material"></td>`;
    tr.innerHTML = pickCell +
      `<td>${esc(m.display_name)}</td><td>${esc(m.company)}</td><td>${esc(m.category)}</td><td>${esc(m.type)}</td>` +
      `<td>${esc(fmt([m.lambda_min, m.lambda_max]))}</td><td>${esc(fmt([m.thick_min, m.thick_max], 0))}</td>` +
      `<td>${usage}</td>`;
    const [checkbox] = tr.getElementsByTagName("input");
    checkbox.onchange = () => {
      if (checkbox.checked) layerSelections.delete(m.id);   // parent checked = all layers
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
  if (!selected.size) return setStatus("select at least one material");
  const items = [...selected].map((id) => {
    const layers = layerSelections.get(id);
    return layers && layers.size ? { id, layer_ids: [...layers] } : { id };
  });
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

function setStatus(text) { $("status").textContent = text; return text; }

$("export").onclick = () => pickIds("export").catch((err) => setStatus(err.message));
$("pick").onclick = () => pickIds("pick").catch((err) => setStatus(err.message));
$("open").onclick = () => openSession().catch((err) => setStatus(err.message));
$("save").onclick = () => saveSession().catch((err) => setStatus(err.message));
$("refresh").onclick = async () => {
  const report = await api("/api/refresh", { method: "POST", body: "{}" });
  setStatus(`cache refreshed: ${report.existing} unchanged, ${report.updated.length} updated`);
};

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
});

$("country").addEventListener("change", async () => {
  await api("/api/config", { method: "POST", body: JSON.stringify({ country: $("country").value }) });
  detailCache.clear();
  await loadMaterials();
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
