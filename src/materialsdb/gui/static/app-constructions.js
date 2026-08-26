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
  const detail = await api(`/api/materials/${encodeURIComponent(materialId)}`);
  const raw = (detail.layers || []).map((l) => l.thick).filter((t) => t !== null && t !== undefined);
  const anyThickness = raw.some((t) => Number(t) === 0);
  const choices = [...new Set(raw.filter((t) => Number(t) > 0).map(Number))].sort((a, b) => a - b);
  // category / own color for preview coloring
  const category = detail.category || detail.group || "Others";
  const ownColor = detail.color ?? detail.information?.color ?? null;
  return { choices_mm: choices, anyThickness, category, ownColor, display_name: detail.display_name || detail.names?.[""] || "" };
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
    const bg = (typeof PickerCore !== "undefined"
      ? PickerCore.categoryColorStyle(layer.category || "Others", layer.ownColor)
      : "#eef");
    return `<div class="stackbar-seg" style="flex:${flex};background:${bg};border-right:2px solid #fff" title="${esc(name)} \u2014 ${esc(fmt(layer.thickness_m * 1000, 0))} mm">` +
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
  const rows = layers.map((layer, index) => {
    const selectedAttr = index === selectedRow ? ' style="background:#eef"' : "";
    const catColor = typeof PickerCore !== "undefined"
      ? PickerCore.categoryColorStyle(layer.category || "Others", layer.ownColor)
      : "#fff";
    return `<tr data-index="${index}"${selectedAttr} style="cursor:pointer;border-left:4px solid ${catColor}">` +
      `<td>${index + 1}</td><td data-role="name">${esc(layer.display_name || layer.material_id)}</td>` +
      `<td>${thicknessCellHtml(layer, index)}</td>` +
      `<td data-role="lambda">${esc(layer.lambda_value ?? "")}</td><td data-role="r">${esc(fmtR(layer))}</td><td></td></tr>`;
  }).join("");
  tbody.innerHTML = rsBoundaryRow("exterior") + rows + rsBoundaryRow("interior");
  bindThicknessControls();
  tbody.querySelectorAll("tr[data-index]").forEach((tr) => {
    tr.addEventListener("click", (event) => {
      if (event.target.tagName === "INPUT" || event.target.tagName === "SELECT") return;
      selectedRow = Number(tr.dataset.index);
      renderLayers();
    });
  });
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
      layers.filter((l) => l.material_id === c.material_id).forEach((row) => {
        row.display_name = c.name || row.display_name;
        row.lambda_value = c.lambda_value;
      });
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
    `<li><a href="#" data-name="${esc(name)}">${esc(name)}</a></li>`).join("") ||
    `<li style="color:#777;font-size:.8rem">none saved yet.<br>Create one with <b>new</b> + <b>add layer</b>, or duplicate a materialsdb construction below.</li>`;
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

  const companySelect = $("mdb-company");
  const companies = [...new Set(materials.map((m) => m.company))].sort();
  companySelect.innerHTML = `<option value="">all companies</option>` +
    companies.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("");

  const renderMdbList = () => {
    const needle = $("mdb-search").value.trim().toLowerCase();
    const company = companySelect.value;
    const visible = materialsdbConstructions.filter((m) =>
      (!company || m.company === company) &&
      (!needle || m.display_name.toLowerCase().includes(needle)));
    $("mdb-list").innerHTML = visible.map((m) =>
      `<li><a href="#" data-id="${esc(m.id)}">${esc(m.display_name)}</a> ` +
      `<span style="color:#888">· ${esc(m.company)}</span></li>`).join("") ||
      "<li><i>no match</i></li>";
    $("mdb-list").querySelectorAll("a").forEach((a) => a.addEventListener("click", async (event) => {
      event.preventDefault();
      showLegacy(a.dataset.id);
    }));
  };

  $("mdb-search").addEventListener("input", () => clearTimeout(setTimeout(renderMdbList, 200)) || undefined);
  let searchTimer;
  $("mdb-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(renderMdbList, 200); });
  companySelect.addEventListener("change", renderMdbList);
  renderMdbList();
}

let legacyState = null;   // {payload, variantIndex}

async function showLegacy(materialId) {
  const payload = await api(`/api/constructions/legacy/${encodeURIComponent(materialId)}`);
  legacyState = { payload, materialId, variantIndex: 0 };
  $("composer-ui").style.display = "none";
  const view = $("readonly-ui");
  view.style.display = "block";
  const entry = materialsdbConstructions.find((m) => m.id === materialId);
  const chips = payload.variants.map((v, index) =>
    `<button class="variant-chip" data-variant="${index}" style="background:${index === 0 ? "#eef" : ""}">` +
    `variant ${index + 1}${v.layers.length ? ` (${v.layers.length} layers)` : ""}</button>`).join("");
  view.innerHTML =
    `<div><button id="back-composer">← back to composer</button>` +
    ` <h3 style="display:inline;margin-left:.5rem">${esc(entry ? entry.display_name : materialId)}` +
    ` <span class="warn" title="this content is a non-spec vendor string; display is best-effort">non-spec vendor data</span></h3></div>` +
    `<div style="margin:.3rem 0">consref: <b>${esc(payload.consref) || "-"}</b> · designusage: <b>${esc(payload.designusage) || "-"}</b></div>` +
    `<div id="variant-chips">${chips}</div>` +
    `<div id="variant-body" style="margin-top:.4rem"></div>` +
    `<details style="margin-top:.4rem"><summary>raw vendor string</summary>` +
    `<pre style="white-space:pre-wrap">${esc(payload.raw)}</pre></details>` +
    `<button id="copy-legacy" style="margin-top:.5rem">create editable copy</button>`;
  view.querySelector("#back-composer").onclick = hideLegacy;
  view.querySelector("#variant-chips").addEventListener("click", (event) => {
    if (event.target.dataset.variant === undefined) return;
    legacyState.variantIndex = Number(event.target.dataset.variant);
    view.querySelectorAll(".variant-chip").forEach((chip) =>
      chip.style.background = chip.dataset.variant === String(legacyState.variantIndex) ? "#eef" : "");
    renderLegacyVariant();
  });
  view.querySelector("#copy-legacy").onclick = () => {
    copyLegacyToComposer(materialId, payload);
    hideLegacy();
  };
  renderLegacyVariant();
  setStatus("materialsdb construction loaded (read-only)");
}

function hideLegacy() {
  $("readonly-ui").style.display = "none";
  $("composer-ui").style.display = "flex";
}

function renderLegacyVariant() {
  const { payload, variantIndex } = legacyState;
  const entry = materialsdbConstructions.find((m) => m.id === legacyState.materialId);
  const variant = payload.variants[variantIndex];
  const rows = variant.layers.map((l, li) => {
    const flag = l.resolvable ? "" : ' <span class="warn" title="guid not found in the local index">⚠ unresolved</span>';
    return `<tr><td>${li + 1}</td><td>${esc(l.name || l.guid.slice(0, 8))}${flag}</td>` +
      `<td>${fmt(l.thickness_m * 1000, 0)} mm</td><td>${esc(fmt(l.lambda_value))}</td></tr>`;
  }).join("");
  const uLine = variant.u !== null && variant.u !== undefined ? `${variant.u.toFixed(3)} W/m²K` : "n/a";
  $("variant-body").innerHTML =
    `<table><thead><tr><th>#</th><th>material</th><th>thickness</th><th>λ W/mK</th></tr></thead><tbody>${rows}</tbody></table>` +
    `<p style="margin-top:.4rem"><b>U = ${uLine}</b>${variant.unresolved_count ? ` · ${variant.unresolved_count} unresolvable guid(s) excluded from the sum` : ""}</p>` +
    (entry && entry.company ? `<p style="color:#777">producer: ${esc(entry.company)}</p>` : "");
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
$("add-layer").onclick = () => openChooser(addLayerFromChooser);

async function addLayerFromChooser(picked) {
  const items = Array.isArray(picked) ? picked : [{ material_id: picked }];
  let added = 0;
  for (const it of items) {
    const thicknessHint = it.thickness_m;
    if (thicknessHint == null) {
      if (layers.some((l) => l.material_id === it.material_id)) {
        // whole-material already present — skip duplicate
        continue;
      }
    } else {
      if (layers.some((l) => l.material_id === it.material_id && Math.abs(l.thickness_m - thicknessHint) < 1e-9)) continue;
    }
    const layer = { material_id: it.material_id, thickness_m: thicknessHint != null ? thicknessHint : 0.2 };
    layers.push(layer);
    selectedRow = layers.length - 1;
    added++;
    renderLayers();
    await attachLayerChoices(layer);
    if (thicknessHint == null && !layer.anyThickness && layer.choices_mm && layer.choices_mm.length) {
      layer.thickness_m = layer.choices_mm[0] / 1000;
    }
    renderLayers();
  }
  if (added) await refreshU();
  else if (items.length) setStatus("selected materials already in construction");
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
$("reverse").onclick = () => {
  if (!layers.length) return;
  layers.reverse();
  if (selectedRow !== -1) selectedRow = layers.length - 1 - selectedRow;
  renderLayers(); refreshU();
};
document.getElementById("settings-tab").addEventListener("click", (e) => {
  e.preventDefault();
  const p = document.getElementById("settings-panel");
  p.style.display = p.style.display === "none" ? "block" : "none";
});
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
loadList().then(() => { renderLayers(); renderContributions(); renderPreview(); });

/* --- chooser modal: multi-select materials or specific layers --- */
function openChooser(onPick) {
  const overlay = document.createElement("div");
  overlay.id = "chooser";
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center";
  overlay.innerHTML = `<div style="background:#fff;padding:.75rem;width:30rem;max-height:80vh;display:flex;flex-direction:column">` +
    `<div style="display:flex;gap:.4rem;margin-bottom:.4rem"><input id="chooser-search" placeholder="live search…" style="flex:1">` +
    `<select id="chooser-type"><option value="">all types</option><option>simple</option><option>btk</option><option>construction</option></select></div>` +
    `<div id="chooser-results" style="overflow:auto;flex:1"></div>` +
    `<div style="display:flex;gap:.5rem;margin-top:.5rem"><button id="chooser-add">Add selected</button><button id="chooser-close">close</button></div></div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector("#chooser-close").onclick = close;
  overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });

  const selected = new Map(); // key -> {material_id, thickness_m?}
  const expanded = new Set();
  const detailCacheChooser = new Map();

  async function getDetailCached(id) {
    if (!detailCacheChooser.has(id)) detailCacheChooser.set(id, api(`/api/materials/${encodeURIComponent(id)}`));
    return detailCacheChooser.get(id);
  }

  let debounceTimer;
  const runSearch = async () => {
    const needle = overlay.querySelector("#chooser-search").value.trim();
    const typeVal = overlay.querySelector("#chooser-type").value;
    const params = new URLSearchParams();
    if (needle) params.set("text", needle);
    if (typeVal) params.set("type", typeVal);
    const qs = params.toString() ? `?${params}` : "";
    const { materials } = await api(`/api/materials${qs}`);
    materials.splice(80);
    const box = overlay.querySelector("#chooser-results");
    box.innerHTML = materials.map((m) => {
      const hasLayers = m.type === "simple";
      return `<div class="chooser-row" data-id="${esc(m.id)}" style="padding:.25rem;border-bottom:1px solid #eee">` +
        `<label style="display:flex;align-items:center;gap:.4rem">` +
        `<input type="checkbox" data-material="${esc(m.id)}">` +
        `<span><b>${esc(m.display_name)}</b> · ${esc(m.company)} · ${esc(m.type)}${m.lambda_min !== null ? ` · λ ${esc(m.lambda_min)}` : ""}</span>` +
        (hasLayers ? ` <span class="chooser-expander" data-id="${esc(m.id)}" style="cursor:pointer;margin-left:auto">\u25b8 layers</span>` : "") +
        `</label>` +
        `<div class="chooser-layers" data-parent="${esc(m.id)}" style="display:none;margin-left:1.2rem"></div></div>`;
    }).join("") || `<i style="color:#888">no match</i>`;

    box.querySelectorAll("input[data-material]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const id = cb.dataset.material;
        if (cb.checked) selected.set(id + "|", { material_id: id });
        else selected.delete(id + "|");
        updateAddButton();
      });
    });
    box.querySelectorAll(".chooser-expander").forEach((exp) => {
      exp.addEventListener("click", async () => {
        const id = exp.dataset.id;
        const sub = box.querySelector(`.chooser-layers[data-parent="${CSS.escape(id)}"]`);
        if (sub.style.display !== "none") { sub.style.display = "none"; exp.textContent = "\u25b8 layers"; return; }
        exp.textContent = "\u25be layers";
        sub.style.display = "block";
        if (sub.dataset.loaded) return;
        sub.dataset.loaded = "1";
        const detail = await getDetailCached(id);
        const layers = detail.layers || [];
        if (!layers.length) { sub.innerHTML = `<i style="color:#888">no manufacturer thicknesses</i>`; return; }
        sub.innerHTML = layers.map((l) =>
          `<label style="display:block"><input type="checkbox" data-material="${esc(id)}" data-thick="${l.thick}"> ${esc(String(l.thick))} mm</label>`).join("");
        sub.querySelectorAll("input[data-thick]").forEach((lcb) => {
          lcb.addEventListener("change", () => {
            const key = id + "|" + lcb.dataset.thick;
            if (lcb.checked) selected.set(key, { material_id: id, thickness_m: Number(lcb.dataset.thick) / 1000 });
            else selected.delete(key);
            // uncheck parent whole-material if specific layer checked
            const parentCb = box.querySelector(`input[data-material="${CSS.escape(id)}"]:not([data-thick])`);
            if (parentCb) parentCb.checked = false;
            if (lcb.checked) selected.delete(id + "|");
            updateAddButton();
          });
        });
      });
    });
  };

  function updateAddButton() {
    const btn = overlay.querySelector("#chooser-add");
    btn.textContent = `Add selected (${selected.size})`;
    btn.disabled = selected.size === 0;
  }

  overlay.querySelector("#chooser-add").onclick = () => {
    if (!selected.size) return;
    close();
    onPick([...selected.values()]);
  };

  overlay.querySelector("#chooser-search").addEventListener("input", () => {
    clearTimeout(debounceTimer); debounceTimer = setTimeout(runSearch, 250);
  });
  overlay.querySelector("#chooser-type").addEventListener("change", runSearch);
  runSearch();
}
