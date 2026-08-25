const TOKEN = window.MATERIALSDB_TOKEN;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

let layers = [];          // [{material_id, thickness_m}]
let selectedRow = -1;
let lastResult = null;

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

function renderLayers() {
  const tbody = $("layers");
  tbody.innerHTML = "";
  layers.forEach((layer, index) => {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    if (index === selectedRow) tr.style.background = "#eef";
    tr.innerHTML = `<td>${index + 1}</td><td data-role="name">${esc(layer.display_name || layer.material_id)}</td>` +
      `<td><input type="number" step="1" min="1" value="${Math.round(layer.thickness_m * 1000)}" data-index="${index}" style="width:5rem"> mm</td>` +
      `<td data-role="lambda">${esc(layer.lambda_value ?? "")}</td><td data-role="r">${esc(fmtR(index))}</td><td></td>`;
    tr.addEventListener("click", () => { selectedRow = index; renderLayers(); });
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("input[data-index]").forEach((input) => {
    input.addEventListener("change", () => {
      const mm = Number(input.value);
      if (!mm || mm <= 0) { setStatus("thickness must be > 0"); input.focus(); return; }
      layers[Number(input.dataset.index)].thickness_m = mm / 1000;
      refreshU();
    });
  });
}

function fmtR(index) {
  return lastResult && lastResult.contributions[index] ? lastResult.contributions[index].r.toFixed(3) : "";
}

function renderContributions() {
  if (!lastResult) { $("u-display").textContent = "-"; $("contributions").innerHTML = ""; $("warnings").textContent = ""; return; }
  if (lastResult.u === null) {
    $("u-display").textContent = "?";
    $("warnings").textContent = "layers without lambda: " + lastResult.missing_lambda_ids.length;
  } else {
    $("u-display").textContent = lastResult.u.toFixed(3) + " W/m2K";
    $("warnings").textContent = lastResult.missing_lambda_ids.length ? `warning: ${lastResult.missing_lambda_ids.length} layer(s) without lambda excluded` : "";
  }
  $("contributions").innerHTML = lastResult.contributions.map((c) =>
    `<dt>${esc(c.name || c.material_id.slice(0, 8))} \u2014 ${c.d_m.toFixed(3)} m</dt><dd>R = ${c.r.toFixed(3)}</dd>`).join("");
}

async function refreshU() {
  if (!layers.length) { lastResult = null; renderContributions(); return; }
  const body = {
    construction: {
      name: $("name").value || "draft",
      design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })),
    },
    preset: $("preset").value,
  };
  try {
    lastResult = await api("/api/u_value", { method: "POST", body: JSON.stringify(body) });
    // merge resolved names/lambdas back into editor rows for display
    lastResult.contributions.forEach((c) => {
      const row = layers.find((l) => l.material_id === c.material_id);
      if (row) { row.display_name = c.name || row.display_name; row.lambda_value = c.lambda_value; }
    });
  } catch (err) { setStatus(err.message); }
  renderLayers();
  renderContributions();
}

async function loadList() {
  const { constructions } = await api("/api/constructions");
  $("saved-list").innerHTML = constructions.map((name) =>
    `<li><a href="#" data-name="${esc(name)}">${esc(name)}</a></li>`).join("") || "<li><i>none saved</i></li>";
  $("saved-list").querySelectorAll("a").forEach((a) => a.addEventListener("click", async (event) => {
    event.preventDefault();
    const construction = await api(`/api/constructions/${encodeURIComponent(a.dataset.name)}`);
    $("name").value = construction.name;
    $("design-usage").value = construction.design_usage || "";
    layers = construction.layers;
    selectedRow = -1; lastResult = null;
    await refreshU();
  }));
}

function setStatus(text) { $("status").textContent = text; return text; }

$("new").onclick = () => { layers = []; selectedRow = -1; lastResult = null; $("name").value = ""; renderLayers(); renderContributions(); };
$("delete").onclick = async () => {
  const name = $("name").value; if (!name) return setStatus("nothing loaded");
  await api(`/api/constructions/${encodeURIComponent(name)}`, { method: "DELETE" });
  setStatus(`deleted ${name}`); loadList(); $("new").onclick();
};
/* --- layer chooser modal over /api/materials --- */
function openChooser() {
  const overlay = document.createElement("div");
  overlay.id = "chooser";
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center";
  overlay.innerHTML = `<div style="background:#fff;padding:.75rem;width:26rem;max-height:80vh;display:flex;flex-direction:column">` +
    `<input id="chooser-search" placeholder="live search…" style="margin-bottom:.4rem">` +
    `<div id="chooser-results" style="overflow:auto;flex:1"></div>` +
    `<button id="chooser-close" style="margin-top:.4rem">close</button></div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector("#chooser-close").onclick = close;
  overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });

  const resultsBox = overlay.querySelector("#chooser-results");
  let debounceTimer;
  const runSearch = async () => {
    const needle = overlay.querySelector("#chooser-search").value.trim();
    const { materials } = await api(`/api/materials${needle ? `?text=${encodeURIComponent(needle)}` : ""}`);
    materials.splice(60);   // cap DOM size; refine search for more
    resultsBox.innerHTML = materials.map((m) =>
      `<div class="chooser-row" data-id="${esc(m.id)}" style="cursor:pointer;padding:.15rem;border-bottom:1px solid #eee">` +
      `<b>${esc(m.display_name)}</b> · ${esc(m.company)} · ${esc(m.type)}` +
      `${m.lambda_min !== null ? ` · λ ${esc(m.lambda_min)}` : ""}</div>`).join("") ||
      `<i style="color:#888">no match</i>`;
    resultsBox.querySelectorAll(".chooser-row").forEach((row) => {
      row.addEventListener("click", () => {
        const materialId = row.dataset.id;
        if (layers.some((l) => l.material_id === materialId)) { setStatus("material already in construction"); return; }
        layers.push({ material_id: materialId, thickness_m: 0.2 });
        selectedRow = layers.length - 1;
        close();
        refreshU();
      });
    });
  };
  overlay.querySelector("#chooser-search").addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runSearch, 250);
  });
  runSearch();
}

$("add-layer").onclick = openChooser;
document.querySelector("[data-move=up]").onclick = () => {
  if (selectedRow > 0) { [layers[selectedRow - 1], layers[selectedRow]] = [layers[selectedRow], layers[selectedRow - 1]]; selectedRow -= 1; renderLayers(); refreshU(); }
};
document.querySelector("[data-move=down]").onclick = () => {
  if (selectedRow > -1 && selectedRow < layers.length - 1) { [layers[selectedRow + 1], layers[selectedRow]] = [layers[selectedRow], layers[selectedRow + 1]]; selectedRow += 1; renderLayers(); refreshU(); }
};
document.querySelector("[data-action=remove]").onclick = () => {
  if (selectedRow > -1) { layers.splice(selectedRow, 1); selectedRow = -1; renderLayers(); refreshU(); }
};
$("preset").onchange = refreshU;
$("design-usage").onchange = refreshU;
$("save").onclick = async () => {
  const name = $("name").value.trim(); if (!name) return setStatus("name required");
  if (!layers.length) return setStatus("add at least one layer");
  await api(`/api/constructions/${encodeURIComponent(name)}`, { method: "POST",
    body: JSON.stringify({ name, design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })) }) });
  setStatus(`saved ${name}`); loadList();
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

loadList();
