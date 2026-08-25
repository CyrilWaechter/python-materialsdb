const TOKEN = window.MATERIALSDB_TOKEN;
const $ = (id) => document.getElementById(id);
const selected = new Set();

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

async function loadTable() {
  const params = new URLSearchParams();
  if ($("text").value) params.set("text", $("text").value);
  if ($("type").value) params.set("type", $("type").value);
  params.set("sort", $("sort").value);
  if ($("desc").checked) params.set("order", "desc");
  const { materials } = await api(`/api/materials?${params}`);
  const rows = $("rows");
  rows.innerHTML = "";
  materials.forEach((m) => {
    const tr = document.createElement("tr");
    const usage = Object.entries(m.usage).filter(([, v]) => v).map(([k]) => k[0].toUpperCase()).join("");
    tr.innerHTML = `<td><input type="checkbox"></td><td></td><td>${m.company}</td>` +
      `<td>${m.category}</td><td>${m.type}</td>` +
      `<td>${fmt([m.lambda_min, m.lambda_max])}</td><td>${fmt([m.thick_min, m.thick_max], 0)}</td><td>${usage}</td>`;
    const [checkbox] = tr.getElementsByTagName("input");
    checkbox.onchange = () => (checkbox.checked ? selected.add(m.id) : selected.delete(m.id));
    tr.addEventListener("click", (event) => {
      if (event.target.tagName === "INPUT") return;
      document.querySelectorAll("tr.selected").forEach((el) => el.classList.remove("selected"));
      tr.classList.add("selected");
      showDetail(m.id);
    });
    rows.appendChild(tr);
  });
  setStatus(`${materials.length} materials`);
}

async function showDetail(id) {
  const m = await api(`/api/materials/${id}`);
  const pairs = [["id", m.id], ["names", JSON.stringify(m.names)], ["descriptions", JSON.stringify(m.descriptions)],
    ["company", `${m.company} (${m.company_id})`], ["category", m.category], ["type", m.type],
    ["lambda", fmt([m.lambda_min, m.lambda_max])], ["thickness mm", fmt([m.thick_min, m.thick_max], 0)],
    ["U-value", fmt(m.u_value_without)], ["consref", m.consref], ["design usage", m.designusage]];
  $("detail").innerHTML = pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<dt>${k}</dt><dd>${String(v)}</dd>`).join("");
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

loadTable();
