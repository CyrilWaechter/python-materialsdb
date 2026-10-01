// materialsdb settings page: general options and colour scheme.
// The palette editor (clone/rename/delete/export/import) is added by later
// tasks; this file owns the shared API helper plus the general/colour-scheme
// controls, the scheme list and the apply-to-model action.
const TOKEN = window.MATERIALSDB_TOKEN;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

let schemesList = [];     // [{name, builtin}] from GET /api/schemes
let activeScheme = null;  // effective scheme, from GET /api/config
let modelPath = null;     // active model path, from GET /api/model/changes

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

function setText(id, text) {
  const target = $(id);
  if (target) target.textContent = text;
  return text;
}

function populateSchemeSelect() {
  const select = $("scheme");
  if (!select) return;
  select.innerHTML = schemesList.map((scheme) =>
    `<option value="${esc(scheme.name)}">${esc(scheme.name)}</option>`).join("");
  if (activeScheme) select.value = activeScheme;
}

function renderSchemeList() {
  const list = $("scheme-list");
  if (!list) return;
  list.innerHTML = schemesList.map((scheme) =>
    `<li data-name="${esc(scheme.name)}"${scheme.name === activeScheme ? ' class="selected"' : ""}>` +
    `${esc(scheme.name)}${scheme.builtin ? '<span class="builtin">built-in</span>' : ""}</li>`).join("");
}

async function saveConfig(patch) {
  return api("/api/config", { method: "POST", body: JSON.stringify(patch) });
}

function applyConfig(cfg) {
  if (!cfg) return;
  if (cfg.lang) {
    const lang = $("lang");
    if (lang) lang.value = cfg.lang;
    if (document.documentElement) document.documentElement.lang = cfg.lang;
  }
  if (cfg.country) {
    const country = $("country");
    if (country) country.value = cfg.country;
  }
  if (cfg.scheme) activeScheme = cfg.scheme;
  const ignore = $("ignore-producer-color");
  if (ignore) ignore.checked = !!cfg.ignore_producer_color;
}

function applyModelState(changes) {
  modelPath = changes && changes.model_path ? changes.model_path : null;
  const button = $("apply-colours");
  const hint = $("apply-hint");
  if (button) button.disabled = !modelPath;
  if (hint) hint.style.display = modelPath ? "none" : "";
}

function selectScheme(name) {
  if (!name) return;
  activeScheme = name;
  const select = $("scheme");
  if (select) select.value = name;
  renderSchemeList();
  saveConfig({ scheme: name }).catch((err) => setText("editor-status", err.message));
}

async function loadSettings() {
  const results = await Promise.allSettled([
    api("/api/config"),
    api("/api/schemes"),
    api("/api/model/changes"),
  ]);
  const message = (reason) => (reason && reason.message ? reason.message : String(reason));
  if (results[1].status === "fulfilled") {
    const payload = results[1].value;
    schemesList = Array.isArray(payload.schemes) ? payload.schemes : [];
    if (payload.error) setText("editor-status", payload.error);
  } else {
    setText("editor-status", `could not load schemes: ${message(results[1].reason)}`);
  }
  applyConfig(results[0].status === "fulfilled" ? results[0].value : null);
  populateSchemeSelect();
  renderSchemeList();
  applyModelState(results[2].status === "fulfilled" ? results[2].value : null);
}

async function applyColours() {
  try {
    const result = await api("/api/model/restyle", { method: "POST", body: JSON.stringify({}) });
    const skipped = Array.isArray(result.skipped) ? result.skipped.length : result.skipped;
    setText("apply-status", `${result.queued} queued, ${skipped} not in store`);
    await refreshApplyStatus();
  } catch (err) {
    setText("apply-status", err.message);
  }
}

async function refreshApplyStatus() {
  try {
    const { clients } = await api("/api/listener/clients");
    const client = (clients || []).find((entry) => entry.model_path && entry.model_path === modelPath);
    const status = client && client.last_status;
    if (status && status.status === "applied" && status.detail) {
      const target = $("apply-status");
      if (target) {
        const current = target.textContent || "";
        target.textContent = current ? `${current} — ${status.detail}` : status.detail;
      }
    }
  } catch {
    /* the listener detail is best-effort */
  }
}

function bindControls() {
  const bind = (id, patchFor) => {
    const control = $(id);
    if (!control) return;
    control.addEventListener("change", () => {
      saveConfig(patchFor(control)).catch((err) => setText("editor-status", err.message));
    });
  };
  bind("lang", (control) => ({ lang: control.value }));
  bind("country", (control) => ({ country: control.value }));
  bind("ignore-producer-color", (control) => ({ ignore_producer_color: control.checked }));

  const select = $("scheme");
  if (select) {
    select.addEventListener("change", () => {
      activeScheme = select.value;
      renderSchemeList();
      saveConfig({ scheme: activeScheme }).catch((err) => setText("editor-status", err.message));
    });
  }

  const applyButton = $("apply-colours");
  if (applyButton) applyButton.addEventListener("click", applyColours);

  const list = $("scheme-list");
  if (list) {
    list.addEventListener("click", (event) => {
      const target = event && event.target;
      const item = target && typeof target.closest === "function" ? target.closest("li[data-name]") : null;
      if (item) selectScheme(item.dataset.name);
    });
  }
}

bindControls();
loadSettings();
