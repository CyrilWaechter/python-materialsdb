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

let allMaterials = [];
let lang = "en";
let sortKey = "company";
let sortAsc = true;
const facetSelections = { company: new Set(), category: new Set(), type: new Set(), usage: new Set() };
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

async function loadMaterials() {
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
      if (key === "lambda" || key === "thick") {
        const numeric = (m) => m[key === "lambda" ? "lambda_min" : "thick_min"];
        allMaterials.sort((a, b) => (numeric(a) ?? Infinity) - (numeric(b) ?? Infinity));
      } else {
        allMaterials.sort((a, b) => String(a[key] ?? "").localeCompare(String(b[key] ?? "")));
      }
      if (sortKey === key) { sortAsc = !sortAsc; allMaterials.reverse(); } else { sortKey = key; sortAsc = true; }
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

function applyModel() {
  const needle = $("text").value.trim().toLowerCase();
  const rowsEl = $("rows");
  rowsEl.innerHTML = "";
  let visible = 0;
  for (const m of allMaterials) {
    if (!matchesFacets(m)) continue;
    if (needle && !(m.display_name.toLowerCase().includes(needle) ||
        m.company.toLowerCase().includes(needle) || m.category.toLowerCase().includes(needle))) continue;
    visible += 1;
    const tr = document.createElement("tr");
    tr.dataset.id = m.id;
    const usage = usageWords(m).map(esc).join(" ");
    tr.innerHTML = `<td><input type="checkbox"></td>` +
      `<td>${esc(m.display_name)}</td><td>${esc(m.company)}</td><td>${esc(m.category)}</td><td>${esc(m.type)}</td>` +
      `<td>${esc(fmt([m.lambda_min, m.lambda_max]))}</td><td>${esc(fmt([m.thick_min, m.thick_max], 0))}</td>` +
      `<td>${usage}</td>`;
    const [checkbox] = tr.getElementsByTagName("input");
    checkbox.onchange = () => (checkbox.checked ? selected.add(m.id) : selected.delete(m.id));
    tr.addEventListener("click", (event) => {
      if (event.target.tagName === "INPUT") return;
      document.querySelectorAll("tr.selected").forEach((el) => el.classList.remove("selected"));
      tr.classList.add("selected");
      showDetail(m.id);
    });
    rowsEl.appendChild(tr);
  }
  if (!visible) rowsEl.innerHTML = `<tr><td colspan="8" style="color:#888">no materials match</td></tr>`;
  setStatus(`${visible} materials`);
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

async function pickIds(action) {
  if (!selected.size) return setStatus("select at least one material");
  if (action === "export") {
    const blob = await api("/api/export", { method: "POST", body: JSON.stringify({ ids: [...selected] }) });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "materialsdb_export.ifc"; link.click();
    URL.revokeObjectURL(url);
  } else if (action === "pick") {
    const result = await api("/api/pick", { method: "POST",
      body: JSON.stringify({ ids: [...selected], replace: $("replace").checked }) });
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
  await loadMaterials();
});

$("country").addEventListener("change", async () => {
  await api("/api/config", { method: "POST", body: JSON.stringify({ country: $("country").value }) });
  await loadMaterials();
});

loadMaterials();
