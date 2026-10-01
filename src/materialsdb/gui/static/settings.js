// materialsdb settings page: general options, colour scheme and palette editor.
// Owns the shared API helper plus the general/colour-scheme controls, the
// scheme list, the editor core (edit/preview/save) and the palette list
// actions (rename/delete/export/import). Also owns the model-target selector
// and the post-apply listener status poll.
const TOKEN = window.MATERIALSDB_TOKEN;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

const PREVIEW_SAMPLES = ["Render", "Masonry", "Insulation", "Wood_Timberproducts", "Concrete"];
const SCHEME_FORMAT = "materialsdb-scheme/1";
const APPLY_STATUS_TIMEOUT_MS = 10000;
const APPLY_STATUS_INTERVAL_MS = 1000;

let schemesList = [];     // [{name, builtin}] from GET /api/schemes
let activeScheme = null;  // effective scheme, from GET /api/config
let modelPath = null;     // active model path, from GET /api/model/changes
let draft = null;         // palette being edited: {name, categories, builtin, dirty}
let pendingImport = null; // parsed import envelope waiting for a free name

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

const deepCopy = (value) => JSON.parse(JSON.stringify(value));

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function hexColor(color) {
  const rgb = Array.isArray(color) ? color : [0, 0, 0];
  const part = (value) => {
    const number = Number(value);
    const clamped = Number.isFinite(number) ? Math.min(255, Math.max(0, Math.round(number))) : 0;
    return clamped.toString(16).padStart(2, "0");
  };
  return `#${part(rgb[0])}${part(rgb[1])}${part(rgb[2])}`;
}

function parseHex(value) {
  const text = String(value ?? "").trim().replace(/^#/, "");
  if (!/^[0-9a-f]{6}$/i.test(text)) return null;
  return [
    parseInt(text.slice(0, 2), 16),
    parseInt(text.slice(2, 4), 16),
    parseInt(text.slice(4, 6), 16),
  ];
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
    `<li data-name="${esc(scheme.name)}" data-builtin="${scheme.builtin ? "true" : "false"}"` +
    `${scheme.name === activeScheme ? ' class="selected"' : ""}>` +
    `${esc(scheme.name)}${scheme.builtin ? '<span class="builtin">built-in &middot; read-only</span>' : ""}</li>`).join("");
}

function updateEditorButtons() {
  const save = $("save-scheme");
  const saveAs = $("save-as-scheme");
  const rename = $("rename-scheme");
  const remove = $("delete-scheme");
  const exportButton = $("export-scheme");
  if (save) save.disabled = !(draft && !draft.builtin);
  if (saveAs) saveAs.disabled = !draft;
  if (exportButton) exportButton.disabled = !draft;
  const custom = !!activeScheme && !schemeIsBuiltin(activeScheme);
  if (rename) rename.disabled = !custom;
  if (remove) remove.disabled = !custom;
}

function schemeIsBuiltin(name) {
  const entry = schemesList.find((scheme) => scheme.name === name);
  return !entry || !!entry.builtin;
}

function renderEditor() {
  const rows = $("palette-rows");
  if (rows) {
    rows.innerHTML = !draft ? "" : Object.keys(draft.categories).map((category) => {
      const hex = hexColor((draft.categories[category] || {}).color);
      return `<span class="category" data-category="${esc(category)}">${esc(category)}</span>` +
        `<input type="color" data-category="${esc(category)}" value="${hex}">` +
        `<input type="text" data-role="hex" data-category="${esc(category)}" value="${hex}">`;
    }).join("");
  }
  updateEditorButtons();
}

function renderPreview() {
  const chips = $("preview-chips");
  const section = $("preview-section");
  const categories = draft ? draft.categories : null;
  if (chips) {
    chips.innerHTML = !categories ? "" : Object.keys(categories).map((category) => {
      const hex = hexColor((categories[category] || {}).color);
      return `<span class="chip" style="background:${hex}" title="${esc(category)} ${hex}">${esc(category)}</span>`;
    }).join("");
  }
  if (section) {
    section.innerHTML = !categories ? "" : PREVIEW_SAMPLES.map((category) => {
      const style = categories[category];
      const hex = style ? hexColor(style.color) : "#ffffff";
      return `<div style="background:${hex}" title="${esc(category)}"></div>`;
    }).join("");
  }
}

function syncRowInput(category, role, hex) {
  const rows = $("palette-rows");
  if (!rows || typeof rows.querySelector !== "function") return;
  const selector = role === "hex"
    ? `input[data-role="hex"][data-category="${category}"]`
    : `input[type="color"][data-category="${category}"]`;
  const input = rows.querySelector(selector);
  if (input) input.value = hex;
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

function basename(path) {
  const parts = String(path ?? "").split(/[\\/]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : String(path ?? "");
}

function applyListenerClients(clients) {
  // only listeners with a connected model are valid restyle targets
  const targets = (clients || []).filter((client) => client && client.model_path);
  const select = $("model-target");
  const row = $("model-target-row");
  const hidden = targets.length ? "" : "none";
  if (row) row.style.display = hidden;
  if (!select) return;
  const previous = select.value;
  select.innerHTML = targets.map((client) =>
    `<option value="${esc(client.client_id)}" title="${esc(client.model_path)}">` +
    `${esc(basename(client.model_path))}</option>`).join("");
  if (targets.some((client) => client.client_id === previous)) select.value = previous;
  else if (targets.length) select.value = targets[0].client_id;
  else select.value = "";
  select.style.display = hidden;
}

async function refreshListenerTargets() {
  try {
    const { clients } = await api("/api/listener/clients");
    applyListenerClients(clients);
  } catch {
    /* best-effort: the next 5 s poll retries */
  }
}

async function refreshModelChanges() {
  try {
    applyModelState(await api("/api/model/changes"));
  } catch {
    /* best-effort: apply-colours keeps its previous enabled state */
  }
}

async function loadDraft(name) {
  if (!name) {
    draft = null;
    renderEditor();
    renderPreview();
    return;
  }
  try {
    const payload = await api(`/api/schemes/${encodeURIComponent(name)}`);
    if (activeScheme !== name) return; // a newer selection won the race
    // built-ins load as an editable draft: the effective palette is copied,
    // but saving must create a new custom scheme (Save stays disabled)
    draft = {
      name: payload.name || name,
      categories: deepCopy(payload.categories || {}),
      builtin: !!payload.builtin,
      dirty: false,
    };
    const field = $("scheme-name");
    if (field) field.value = draft.builtin ? `${draft.name} copy` : draft.name;
  } catch (err) {
    draft = null;
    setText("editor-status", err.message);
  }
  renderEditor();
  renderPreview();
}

async function refreshSchemeState() {
  const results = await Promise.allSettled([api("/api/config"), api("/api/schemes")]);
  const message = (reason) => (reason && reason.message ? reason.message : String(reason));
  if (results[1].status === "fulfilled") {
    const payload = results[1].value;
    if (Array.isArray(payload.schemes)) schemesList = payload.schemes;
    if (payload.error) setText("editor-status", payload.error);
  } else {
    setText("editor-status", `could not refresh schemes: ${message(results[1].reason)}`);
  }
  if (results[0].status === "fulfilled" && results[0].value.scheme) activeScheme = results[0].value.scheme;
  populateSchemeSelect();
  renderSchemeList();
  updateEditorButtons();
}

async function selectScheme(name) {
  if (!name) return;
  if (draft && name === draft.name) return; // already editing this palette: keep the in-memory draft
  if (draft && draft.dirty && !mayDiscardDraft(name)) {
    // cancelled: restore the control to the effective scheme and keep editing
    const select = $("scheme");
    if (select) select.value = activeScheme || draft.name;
    setText("editor-status", `unsaved changes to ${draft.name} kept — save or discard before switching`);
    return;
  }
  activeScheme = name;
  const select = $("scheme");
  if (select) select.value = name;
  renderSchemeList();
  try {
    await saveConfig({ scheme: name });
  } catch (err) {
    setText("editor-status", err.message);
  }
  await loadDraft(name);
}

function mayDiscardDraft(name) {
  if (!draft || !draft.dirty || name === draft.name) return true;
  const confirmFn = typeof window !== "undefined" && typeof window.confirm === "function" ? window.confirm : null;
  if (!confirmFn) return true; // no confirm dialog available: switching stays allowed
  return confirmFn(`Discard unsaved changes to ${draft.name}?`);
}

async function saveDraft(asNewName) {
  if (!draft) {
    setText("editor-status", "select a scheme before saving");
    return;
  }
  const requested = typeof asNewName === "string" ? asNewName.trim() : "";
  const name = requested || String(draft.name || "").trim();
  if (!name) {
    setText("editor-status", "enter a scheme name first");
    return;
  }
  if (requested && requested !== draft.name && schemesList.some((scheme) => scheme.name === requested)) {
    // save-as must create a new palette: never silently overwrite a custom one
    setText("editor-status", `a scheme named ${requested} already exists — choose another name`);
    return;
  }
  try {
    await api(`/api/schemes/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify({ categories: draft.categories }),
    });
  } catch (err) {
    setText("editor-status", err.message);
    return;
  }
  draft.name = name;
  draft.builtin = false;
  draft.dirty = false;
  updateEditorButtons();
  setText("editor-status", `saved ${name}`);
  if (activeScheme !== name) {
    activeScheme = name;
    try {
      await saveConfig({ scheme: name });
    } catch (err) {
      setText("editor-status", err.message);
    }
  }
  await refreshSchemeState();
}

async function renameSelected() {
  const oldName = activeScheme;
  if (!oldName) {
    setText("editor-status", "select a scheme to rename");
    return;
  }
  if (schemeIsBuiltin(oldName)) {
    setText("editor-status", `built-in scheme ${oldName} cannot be renamed`);
    return;
  }
  const field = $("scheme-name");
  const newName = field ? field.value.trim() : "";
  if (!newName) {
    setText("editor-status", "enter a scheme name first");
    return;
  }
  if (newName === oldName) {
    setText("editor-status", `scheme is already named ${newName}`);
    return;
  }
  try {
    await api(`/api/schemes/${encodeURIComponent(oldName)}/rename`, {
      method: "POST",
      body: JSON.stringify({ new_name: newName }),
    });
  } catch (err) {
    setText("editor-status", err.message);
    return;
  }
  await refreshSchemeState();
  await loadDraft(activeScheme);
  setText("editor-status", `renamed ${oldName} to ${newName}`);
}

async function deleteSelected() {
  const name = activeScheme;
  if (!name) {
    setText("editor-status", "select a scheme to delete");
    return;
  }
  if (schemeIsBuiltin(name)) {
    setText("editor-status", `built-in scheme ${name} cannot be deleted`);
    return;
  }
  try {
    await api(`/api/schemes/${encodeURIComponent(name)}/delete`, { method: "POST", body: "{}" });
  } catch (err) {
    setText("editor-status", err.message);
    return;
  }
  await refreshSchemeState();
  await loadDraft(activeScheme);
  setText("editor-status", `deleted ${name}`);
}

function exportPayload() {
  if (!draft) return null;
  const field = $("scheme-name");
  const typed = field && typeof field.value === "string" ? field.value.trim() : "";
  const name = typed || String(draft.name || "").trim();
  if (!name) return null;
  return { format: SCHEME_FORMAT, name, categories: deepCopy(draft.categories) };
}

async function exportScheme() {
  const payload = exportPayload();
  if (!payload) {
    setText("editor-status", "select a scheme before exporting");
    return;
  }
  const text = JSON.stringify(payload, null, 2);
  try {
    if (typeof window.showSaveFilePicker === "function") {
      const handle = await window.showSaveFilePicker({
        suggestedName: `${payload.name}.json`,
        types: [{ description: "materialsdb scheme", accept: { "application/json": [".json"] } }],
      });
      const writable = await handle.createWritable();
      await writable.write(text);
      await writable.close();
      setText("editor-status", `exported ${payload.name}.json`);
      return;
    }
    const chosen = typeof window.prompt === "function" ? window.prompt("file name", `${payload.name}.json`) : null;
    if (!chosen) return; // cancelled: not an error
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = chosen;
    link.click();
    URL.revokeObjectURL(url);
    setText("editor-status", `exported ${chosen}`);
  } catch (err) {
    if (err && err.name === "AbortError") return; // picker cancelled: not an error
    setText("editor-status", `export failed: ${err && err.message ? err.message : err}`);
  }
}

function resetImportFile() {
  const file = $("import-file");
  if (file) file.value = "";
}

async function postImport(name) {
  if (!pendingImport) return;
  const envelope = { ...pendingImport, name };
  try {
    const result = await api("/api/schemes/import", { method: "POST", body: JSON.stringify(envelope) });
    const imported = (result && result.name) || name;
    pendingImport = null;
    const label = $("import-name-label");
    if (label) label.style.display = "none";
    const nameField = $("import-name");
    if (nameField) nameField.value = "";
    resetImportFile();
    setText("editor-status", `imported ${imported}`);
    await refreshSchemeState();
    await selectScheme(imported);
  } catch (err) {
    // 409 (name taken): the wrapper stays visible so the name can be edited and retried
    setText("editor-status", `import failed: ${err.message} — edit the name and press import again`);
  }
}

async function importPalette(file) {
  if (!file) return;
  let envelope;
  try {
    envelope = JSON.parse(await file.text());
  } catch (err) {
    setText("editor-status", `import failed: not valid JSON (${err.message})`);
    resetImportFile();
    return;
  }
  if (!envelope || envelope.format !== SCHEME_FORMAT) {
    setText("editor-status", `import failed: expected a ${SCHEME_FORMAT} payload`);
    resetImportFile();
    return;
  }
  pendingImport = envelope;
  const nameField = $("import-name");
  if (nameField) nameField.value = String(envelope.name ?? "");
  const label = $("import-name-label");
  if (label) label.style.display = "";
  await postImport(nameField ? nameField.value.trim() : "");
}

async function loadSettings() {
  const results = await Promise.allSettled([
    api("/api/config"),
    api("/api/schemes"),
    api("/api/model/changes"),
    api("/api/listener/clients"),
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
  if (results[3].status === "fulfilled") applyListenerClients(results[3].value.clients);
  await loadDraft(activeScheme);
}

function appendApplyStatus(detail) {
  if (!detail) return;
  const target = $("apply-status");
  if (!target) return;
  const current = target.textContent || "";
  target.textContent = current ? `${current} — ${detail}` : detail;
}

async function refreshApplyStatus() {
  // legacy fallback (no explicit target): match the active model's listener
  try {
    const { clients } = await api("/api/listener/clients");
    const client = (clients || []).find((entry) => entry.model_path && entry.model_path === modelPath);
    const status = client && client.last_status;
    if (status && status.status === "applied" && status.detail) appendApplyStatus(status.detail);
  } catch {
    /* the listener detail is best-effort */
  }
}

async function pollApplyStatus(clientId) {
  const deadline = Date.now() + APPLY_STATUS_TIMEOUT_MS;
  for (;;) {
    let clients = null;
    try {
      ({ clients } = await api("/api/listener/clients"));
    } catch {
      /* transient failure: retry until the deadline */
    }
    const client = (clients || []).find((entry) => entry && entry.client_id === clientId);
    const status = client && client.last_status;
    if (status && status.status && status.status !== "pending") {
      appendApplyStatus(status.detail);
      return;
    }
    if (Date.now() >= deadline) return;
    await sleep(APPLY_STATUS_INTERVAL_MS);
  }
}

async function applyColours() {
  const select = $("model-target");
  const clientId = select ? String(select.value || "") : "";
  const body = clientId ? { client_id: clientId } : {};
  let result;
  try {
    result = await api("/api/model/restyle", { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    setText("apply-status", err.message);
    return;
  }
  const skipped = Array.isArray(result.skipped) ? result.skipped.length : result.skipped;
  setText("apply-status", `${result.queued} queued, ${skipped} not in store`);
  if (!clientId) {
    await refreshApplyStatus();
    return;
  }
  if (result.queued > 0) await pollApplyStatus(clientId);
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
  if (select) select.addEventListener("change", () => selectScheme(select.value));

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

  const saveButton = $("save-scheme");
  if (saveButton) saveButton.addEventListener("click", () => saveDraft());

  const saveAsButton = $("save-as-scheme");
  if (saveAsButton) {
    saveAsButton.addEventListener("click", () => {
      const field = $("scheme-name");
      const name = field ? field.value.trim() : "";
      if (!name) {
        setText("editor-status", "enter a scheme name first");
        return;
      }
      saveDraft(name);
    });
  }

  const renameButton = $("rename-scheme");
  if (renameButton) renameButton.addEventListener("click", () => renameSelected());

  const deleteButton = $("delete-scheme");
  if (deleteButton) deleteButton.addEventListener("click", () => deleteSelected());

  const exportButton = $("export-scheme");
  if (exportButton) exportButton.addEventListener("click", () => exportScheme());

  const importButton = $("import-scheme");
  const importFile = $("import-file");
  if (importButton) {
    importButton.addEventListener("click", () => {
      if (pendingImport) {
        const field = $("import-name");
        const name = field ? field.value.trim() : "";
        if (!name) {
          setText("editor-status", "enter a scheme name first");
          return;
        }
        postImport(name);
        return;
      }
      if (importFile && typeof importFile.click === "function") importFile.click();
    });
  }
  if (importFile) {
    importFile.addEventListener("change", () => {
      const file = importFile.files && importFile.files[0];
      if (file) importPalette(file);
    });
  }

  const rows = $("palette-rows");
  if (rows) {
    rows.addEventListener("input", (event) => {
      if (!draft) return;
      const target = event && event.target ? event.target : {};
      const dataset = target.dataset || {};
      const category = dataset.category;
      if (!category || !draft.categories[category]) return;
      const color = parseHex(target.value);
      if (!color) return;
      draft.categories[category].color = color;
      draft.dirty = true;
      syncRowInput(category, dataset.role === "hex" ? "color" : "hex", hexColor(color));
      renderPreview();
    });
  }

  updateEditorButtons();
}

if (window && typeof window.addEventListener === "function") {
  window.addEventListener("beforeunload", (event) => {
    if (!draft || !draft.dirty) return undefined;
    event.preventDefault();
    event.returnValue = true;
    return true;
  });
}

bindControls();
loadSettings();
setInterval(() => {
  refreshListenerTargets();
  refreshModelChanges();
}, 5000);
