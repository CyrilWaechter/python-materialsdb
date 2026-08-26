const TOKEN = window.MATERIALSDB_TOKEN;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

let layers = [];          // [{material_id, thickness_m, display_name?, lambda_value?, choices_mm?, anyThickness?}]
let selectedRow = -1;
let lastResult = null;
let currentPreset = "ISO6946";

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

/* ---------- thickness cell: free input + manufacturer dropdown + warning ---------- */

function thicknessCellHtml(layer, index) {
  const mm = Math.round(layer.thickness_m * 1000 * 1000) / 1000;
  const choices = layer.choices_mm || [];
  let control;
  if (choices.length > 1) {
    const options = choices.map((c) =>
      `<option value="${c}"${Math.abs(c - mm) < 1e-6 ? " selected" : ""}>${c} mm</option>`).join("");
    const customSelected = !choices.some((c) => Math.abs(c - mm) < 1e-6);
    control = `<select data-role="choice" data-index="${index}" style="max-width:7rem">
        ${customSelected ? `<option value="" selected>custom</option>` : ""}${options}</select>
      <input type="text" inputmode="decimal" data-role="thick" data-index="${index}"
             value="${mm}" style="width:4.5rem" title="thickness in mm">`;
  } else {
    control = `<input type="text" inputmode="decimal" data-role="thick" data-index="${index}"
             value="${mm}" style="width:4.5rem" title="thickness in mm">`;
  }
  const warned = choices.length > 0 && !layer.anyThickness &&
                 !choices.some((c) => Math.abs(c - mm) < 1e-6);
  return `${control} mm` +
    (warned ? ` <span class="warn" title="the manufacturer does not offer this thickness">\u26a0</span>` : "");
}

function bindThicknessControls() {
  $("layers").querySelectorAll("select[data-role=choice]").forEach((select) => {
    select.addEventListener("change", () => {
      const index = Number(select.dataset.index);
      if (select.value !== "") {
        layers[index].thickness_m = Number(select.value) / 1000;
        renderLayers();
        refreshU();
      }
    });
  });
  $("layers").querySelectorAll("input[data-role=thick]").forEach((input) => {
    input.addEventListener("change", () => {
      const index = Number(input.dataset.index);
      const mm = Number(input.value.replace(",", "."));
      if (!mm || mm <= 0) { setStatus("thickness must be > 0"); input.focus(); return; }
      layers[index].thickness_m = mm / 1000;
      renderLayers();
      refreshU();
    });
  });
}

async function fetchLayerChoices(materialId) {
  // manufacturer thicknesses (mm) from the material detail; a source layer
  // with thick=0 means any thickness is acceptable.
  const detail = await api(`/api/materials/${encodeURIComponent(materialId)}`);
  const raw = (detail.layers || []).map((l) => l.thick).filter((t) => t !== null && t !== undefined);
  const anyThickness = raw.some((t) => Number(t) === 0);
  const choices = [...new Set(raw.filter((t) => Number(t) > 0).map(Number))].sort((a, b) => a - b);
  return { choices_mm: choices, anyThickness };
}

/* ---------- scaled stack preview + Rsi/Rse boundaries ---------- */

function renderPreview() {
  const drawer = $("preview");
  if (!drawer) return;
  const labels = i18nLabels();
  const total = layers.reduce((sum, l) => sum + l.thickness_m, 0);
  let inner = "";
  const rsiKnown = lastResult ? lastResult.rsi : null;
  const rseKnown = lastResult ? lastResult.rse : null;
  inner += layers.map((layer, index) => {
    const flex = total ? (layer.thickness_m / total) * 100 : 0;
    const name = layer.display_name || layer.material_id.slice(0, 8);
    return `<div class="stackbar-seg" style="flex:${flex}" title="${esc(name)} \u2014 ${esc(fmt(layer.thickness_m * 1000, 0))} mm">` +
      `${esc(String(index + 1))}<br>${fmt(layer.thickness_m * 1000, 0)} mm</div>`;
  }).join("");
  drawer.innerHTML =
    `<span class="rs-chip">${esc(labels.exterior)} \u00b7 Rse = ${rseKnown ?? "?"}</span>` +
    `<div class="stackbar">${inner || "<i style='color:#888'>no layers</i>"}</div>` +
    `<span class="rs-chip">${esc(labels.interior)} \u00b7 Rsi = ${rsiKnown ?? "?"}</span>`;
}

function i18nLabels() {
  const lang = (document.documentElement.lang || "en").slice(0, 2);
  const dict = {
    en: { interior: "Interior", exterior: "Exterior", formula: "How U is computed",
          warnOffered: "manufacturer does not offer this thickness" },
    fr: { interior: "Int\u00e9rieur", exterior: "Ext\u00e9rieur", formula: "Calcul du U",
          warnOffered: "\u00e9paisseur non propos\u00e9e par le fabricant" },
    de: { interior: "Innen", exterior: "Au\u00dfen", formula: "U-Berechnung",
          warnOffered: "Dicke vom Hersteller nicht angeboten" },
  };
  return dict[lang] || dict.en;
}

/* ---------- table rendering ---------- */

function renderLayers() {
  const tbody = $("layers");
  tbody.innerHTML = rsBoundaryRow("exterior") + layers.map((layer, index) => {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    if (index === selectedRow) tr.style.background = "#eef";
    tr.innerHTML = `<td>${index + 1}</td><td data-role="name">${esc(layer.display_name || layer.material_id)}</td>` +
      `<td>${thicknessCellHtml(layer, index)}</td>` +
      `<td data-role="lambda">${esc(layer.lambda_value ?? "")}</td><td data-role="r">${esc(fmtR(layer))}</td><td></td>`;
    tr.addEventListener("click", () => { selectedRow = index; renderLayers(); });
    tbody.appendChild(tr);
  });
  tbody.insertAdjacentHTML("beforeend", rsBoundaryRow("interior"));
  bindThicknessControls();
}

function rsBoundaryRow(which) {
  const presetName = $("preset").value;
  const values = lastResult ? [lastResult.rsi, lastResult.rse] : null;
  const label = i18nLabels()[which];
  const value = which === "interior"
    ? (values ? values[0] : RESISTANCE_DISPLAY[presetName]?.rsi)
    : (values ? values[1] : RESISTANCE_DISPLAY[presetName]?.rse);
  return `<tr class="rsrow"><td colspan="2">${esc(label)}</td>` +
    `<td colspan="3">R${which === "interior" ? "si" : "se"} = ${value ?? "?"} m\u00b2K/W</td><td></td></tr>`;
}

const RESISTANCE_DISPLAY = {
  ISO6946: { rsi: 0.13, rse: 0.04 },
  SIA180: { rsi: 0.13, rse: 0.04 },
};

function fmtR(layer) {
  const c = lastResult && lastResult.contributions.find((x) => x.material_id === layer.material_id);
  return c ? c.r.toFixed(3) : "";
}

function renderContributions() {
  if (!lastResult) { $("u-display").textContent = "-"; $("contributions").innerHTML = ""; $("warnings").textContent = ""; }
  else if (lastResult.u === null) {
    $("u-display").textContent = "?";
    $("warnings").textContent = "layers without lambda or invalid thickness excluded: " + lastResult.missing_lambda_ids.length;
  } else {
    $("u-display").textContent = lastResult.u.toFixed(3) + " W/m2K";
    $("warnings").textContent = lastResult.missing_lambda_ids.length ? `warning: ${lastResult.missing_lambda_ids.length} layer(s) without lambda excluded` : "";
  }
  $("contributions").innerHTML = (lastResult ? lastResult.contributions : []).map((c) =>
    `<dt>${esc(c.name || c.material_id.slice(0, 8))} \u2014 ${c.d_m.toFixed(3)} m</dt><dd>R = ${c.r.toFixed(3)}</dd>`).join("");
}

async function refreshU() {
  if (!layers.length) { lastResult = null; renderContributions(); renderPreview(); return; }
  const body = {
    construction: {
      name: $("name").value || "draft",
      design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })),
    },
    preset: $("preset").value,
  };
  currentPreset = $("preset").value;
  try {
    lastResult = await api("/api/u_value", { method: "POST", body: JSON.stringify(body) });
    lastResult.contributions.forEach((c) => {
      const row = layers.find((l) => l.material_id === c.material_id);
      if (row) { row.display_name = c.name || row.display_name; row.lambda_value = c.lambda_value; }
    });
  } catch (err) { setStatus(err.message); }
  renderLayers();
  renderContributions();
  renderPreview();
}

/* ---------- layer choices after add ---------- */

async function attachLayerChoices(layer) {
  try {
    Object.assign(layer, await fetchLayerChoices(layer.material_id));
  } catch { /* choices are optional */ }
  renderLayers();
}

/* ---------- saved + materialsdb lists ---------- */

async function loadList() {
  const { constructions } = await api("/api/constructions");
  $("saved-list").innerHTML = constructions.map((name) =>
    `<li><a href="#" data-name="${esc(name)}">${esc(name)}</a></li>`).join("") || "<li><i>none saved</i></li>";
  $("saved-list").querySelectorAll("a").forEach((a) => a.addEventListener("click", async (event) => {
    event.preventDefault();
    const construction = await api(`/api/constructions/${encodeURIComponent(a.dataset.name)}`);
    loadEditable(construction);
  }));
  await loadMaterialsdbConstructions();
}

let materialsdbConstructions = [];

async function loadMaterialsdbConstructions() {
  const { materials } = await api("/api/materials?type=construction");
  materialsdbConstructions = materials;
  $("mdb-list").innerHTML = materials.map((m) =>
    `<li><a href="#" data-id="${esc(m.id)}">${esc(m.display_name)}</a></li>`).join("") ||
    "<li><i>none in catalog</i></li>";
  $("mdb-list").querySelectorAll("a").forEach((a) => a.addEventListener("click", async (event) => {
    event.preventDefault();
    showLegacy(a.dataset.id);
  }));
}

async function showLegacy(materialId) {
  const payload = await api(`/api/constructions/legacy/${encodeURIComponent(materialId)}`);
  const entry = materialsdbConstructions.find((m) => m.id === materialId);
  const view = $("legacy-view");
  const variantBlocks = payload.variants.map((variant, index) => {
    const rows = variant.layers.map((l) => {
      const flag = l.resolvable ? "" : " \u26a0 unresolvable guid";
      return `<tr><td>${esc(l.name || l.guid.slice(0, 8))}</td>` +
        `<td>${fmt(l.thickness_m * 1000, 0)} mm</td><td>${esc(fmt(l.lambda_value))}</td><td>${flag}</td></tr>`;
    }).join("");
    return `<div class="label">variant ${index + 1}${variant.header_raw ? ` (header: ${esc(variant.header_raw)})` : ""}` +
      `${variant.u !== null && variant.u !== undefined ? ` \u00b7 U = ${variant.u.toFixed(3)} W/m2K` : " \u00b7 U n/a"}</div>` +
      `<table style="width:100%">${rows}</table>`;
  }).join("");
  view.innerHTML =
    `<h4>${esc(entry ? entry.display_name : materialId)} <span class="warn">non-spec vendor string</span></h4>` +
    `<div>consref: ${esc(payload.consref) || "-"} \u00b7 designusage: ${esc(payload.designusage) || "-"}</div>` +
    variantBlocks +
    `<details style="margin-top:.4rem"><summary>raw</summary><pre style="white-space:pre-wrap">${esc(payload.raw)}</pre></details>` +
    `<button id="copy-legacy">create editable copy</button>`;
  view.querySelector("#copy-legacy").onclick = () => copyLegacyToComposer(materialId, payload);
  setStatus(`materialsdb construction loaded (read-only)`);
}

async function copyLegacyToComposer(materialId, payload) {
  const variant = payload.variants.find((v) => v.layers.every((l) => l.resolvable)) || payload.variants[0];
  layers = variant.layers
    .filter((l) => l.resolvable)
    .map((l) => ({ material_id: l.guid, thickness_m: l.thickness_m }));
  selectedRow = -1;
  $("name").value = entry_name(materialId) || materialId.slice(0, 8);
  const usageMap = { consDesignForWall: "consDesignForWall", consDesignForRoof: "consDesignForRoof", consDesignForFloor: "consDesignForFloor" };
  $("design-usage").value = usageMap[payload.designusage] || "";
  await Promise.all(layers.map(attachLayerChoices));
  await refreshU();
  setStatus("editable copy created \u2014 adjust and save under your own name");
}

function entry_name(materialId) {
  const entry = materialsdbConstructions.find((m) => m.id === materialId);
  return entry ? entry.display_name : null;
}

function loadEditable(construction) {
  $("name").value = construction.name;
  $("design-usage").value = construction.design_usage || "";
  layers = construction.layers.map((l) => ({ ...l }));
  selectedRow = -1; lastResult = null;
  layerChoicesInitAll();
}

async function layerChoicesInitAll() {
  await Promise.all(layers.map(async (layer) => { Object.assign(layer, await fetchLayerChoices(layer.material_id)); }));
  await refreshU();
}

function setStatus(text) { $("status").textContent = text; return text; }

$("new").onclick = () => { layers = []; selectedRow = -1; lastResult = null; $("name").value = ""; renderLayers(); renderContributions(); renderPreview(); };
$("delete").onclick = async () => {
  const name = $("name").value; if (!name) return setStatus("nothing loaded");
  await api(`/api/constructions/${encodeURIComponent(name)}`, { method: "DELETE" });
  setStatus(`deleted ${name}`); loadList(); $("new").onclick();
};
$("add-layer").onclick = async () => openChooserWithChoices();

async function openChooserWithChoices() {
  const overlay = openChooser((materialId) => {
    const layer = layers.find((l) => l.material_id === materialId);
    if (layer) attachLayerChoices(layer);
  });
  return overlay;
}
document.querySelector("[data-move=up]").onclick = () => {
  if (selectedRow > 0) { [layers[selectedRow - 1], layers[selectedRow]] = [layers[selectedRow], layers[selectedRow - 1]]; selectedRow -= 1; renderLayers(); refreshU(); }
};
document.querySelector("[data-move=down]").onclick = () => {
  if (selectedRow > -1 && selectedRow < layers.length - 1) { [layers[selectedRow + 1], layers[selectedRow]] = [layers[selectedRow], layers[selectedRow + 1]]; selectedRow += 1; renderLayers(); refreshU(); }
};
document.querySelector("[data-action=remove]").onclick = () => {
  if (selectedRow > -1) { layers.splice(selectedRow, 1); selectedRow = -1; renderLayers(); refreshU(); }
};
$("preset").onchange = () => { refreshU(); };
$("design-usage").onchange = refreshU;
$("save").onclick = async () => {
  const name = $("name").value.trim(); if (!name) return setStatus("name required");
  if (!layers.length) return setStatus("add at least one layer");
  await api(`/api/constructions/${encodeURIComponent(name)}`, { method: "POST",
    body: JSON.stringify({ name, design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })) }) });
  setStatus(`saved ${name} (overwrites same-name construction)`); loadList();
};
$("export-ifc").onclick = async () => {
  const name = $("name").value.trim(); if (!name || !layers.length) return setStatus("nothing to export");
  const blob = await api("/api/export-construction", { method: "POST",
    body: JSON.stringify({ construction: { name, design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })) } }) });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a"); link.href = url; link.download = `${name}.ifc`; link.click();
  URL.revokeObjectURL(url);
};
$("append-session").onclick = async () => {
  if (!$("name").value || !layers.length) return setStatus("nothing to append");
  const result = await api("/api/append-construction", { method: "POST",
    body: JSON.stringify({ construction: { name: $("name").value,
      design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })) } }),
    });
  setStatus(`appended layer set (${result.layer_count} layers)`);
};
$("formula-toggle").onclick = () => { $("formula-box").style.display = $("formula-box").style.display === "none" ? "block" : "none"; };

loadList().then(() => { renderLayers(); renderContributions(); renderPreview(); });

/* --- chooser modal (calls onPick with chosen material guid) --- */
function openChooser(onPick) {
  const overlay = document.createElement("div");
  overlay.id = "chooser";
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center";
  overlay.innerHTML = `<div style="background:#fff;padding:.75rem;width:26rem;max-height:80vh;display:flex;flex-direction:column">` +
    `<input id="chooser-search" placeholder="live search…" style="margin-bottom:.4rem">` +
    `<div id="chooser-results" style="overflow:auto;flex:1"></div>` +
    `<button id="chooser-close">close</button></div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector("#chooser-close").onclick = close;
  overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });

  let debounceTimer;
  const runSearch = async () => {
    const needle = overlay.querySelector("#chooser-search").value.trim();
    const { materials } = await api(`/api/materials${needle ? `?text=${encodeURIComponent(needle)}` : ""}`);
    materials.splice(60);
    const box = overlay.querySelector("#chooser-results");
    box.innerHTML = materials.map((m) =>
      `<div class="chooser-row" data-id="${esc(m.id)}" style="cursor:pointer;padding:.15rem;border-bottom:1px solid #eee">` +
      `<b>${esc(m.display_name)}</b> · ${esc(m.company)} · ${esc(m.type)}` +
      `${m.lambda_min !== null ? ` · λ ${esc(m.lambda_min)}` : ""}</div>`).join("") ||
      `<i style="color:#888">no match</i>`;
    box.querySelectorAll(".chooser-row").forEach((row) => row.addEventListener("click", () => {
      close();
      onPick(row.dataset.id);
    }));
  };
  overlay.querySelector("#chooser-search").addEventListener("input", () => {
    clearTimeout(debounceTimer); debounceTimer = setTimeout(runSearch, 250);
  });
  runSearch();
}
