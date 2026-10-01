// materialsdb settings page: general options, colour scheme and palette editor.
// Owns the shared API helper plus the general/colour-scheme controls, the
// scheme list, the editor core (clone/edit/preview/save) and the palette list
// actions (rename/delete/export/import).
const TOKEN = window.MATERIALSDB_TOKEN;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

const PREVIEW_SAMPLES = ["Render", "Masonry", "Insulation", "Wood_Timberproducts", "Concrete"];
const SCHEME_FORMAT = "materialsdb-scheme/1";

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
  const clone = $("clone-scheme");
  const rename = $("rename-scheme");
  const remove = $("delete-scheme");
  if (save) save.disabled = !(draft && !draft.builtin);
  if (saveAs) saveAs.disabled = !draft;
  if (clone) clone.disabled = !activeScheme;
  const custom = !!activeScheme && !schemeIsBuiltin(activeScheme);
  if (rename) rename.disabled = !custom;
  if (remove) remove.disabled = !custom;
}

function schemeIsBuiltin(name) {
  const entry = schemesList.find((scheme) => scheme.name === name);
  return !entry || !!entry.builtin;
}

function clearExportLink() {
  const target = $("export-scheme");
  if (target) target.innerHTML = "export";
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

async function loadDraft(name) {
  const entry = schemesList.find((scheme) => scheme.name === name);
  if (!name || (entry && entry.builtin)) {
    draft = null;
    renderEditor();
    renderPreview();
    return;
  }
  try {
    const payload = await api(`/api/schemes/${encodeURIComponent(name)}`);
    if (activeScheme !== name) return; // a newer selection won the race
    draft = payload.builtin
      ? null
      : { name: payload.name || name, categories: deepCopy(payload.categories || {}), builtin: false, dirty: false };
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
  clearExportLink();
}

async function selectScheme(name) {
  if (!name) return;
  activeScheme = name;
  const select = $("scheme");
  if (select) select.value = name;
  renderSchemeList();
  clearExportLink();
  try {
    await saveConfig({ scheme: name });
  } catch (err) {
    setText("editor-status", err.message);
  }
  await loadDraft(name);
}

async function cloneSelected() {
  const name = activeScheme;
  if (!name) {
    setText("editor-status", "select a scheme to clone");
    return;
  }
  try {
    const payload = await api(`/api/schemes/${encodeURIComponent(name)}`);
    draft = {
      name: payload.name || name,
      categories: deepCopy(payload.categories || {}),
      builtin: !!payload.builtin,
      dirty: true,
    };
    const field = $("scheme-name");
    if (field) field.value = `${draft.name} copy`;
    renderEditor();
    renderPreview();
    setText("editor-status", `cloned ${name} — edit, then save as a new name`);
  } catch (err) {
    setText("editor-status", err.message);
  }
}

async function saveDraft(asNewName) {
  if (!draft) {
    setText("editor-status", "clone a scheme before saving");
    return;
  }
  const requested = typeof asNewName === "string" ? asNewName.trim() : "";
  const name = requested || String(draft.name || "").trim();
  if (!name) {
    setText("editor-status", "enter a scheme name first");
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

function exportSelected() {
  const name = activeScheme;
  if (!name) {
    setText("editor-status", "select a scheme to export");
    return;
  }
  const target = $("export-scheme");
  if (!target) return;
  // the GET export is not token-gated, so a plain download anchor works
  const href = `/api/schemes/${encodeURIComponent(name)}/export`;
  target.innerHTML = `<a href="${esc(href)}" download="${esc(name)}.json">download ${esc(name)}.json</a>`;
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
  await loadDraft(activeScheme);
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

  const cloneButton = $("clone-scheme");
  if (cloneButton) cloneButton.addEventListener("click", () => cloneSelected());

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
  if (exportButton) {
    exportButton.addEventListener("click", (event) => {
      const target = event && event.target;
      // a click on the rendered download anchor must act, not re-render it
      if (target && typeof target.closest === "function" && target.closest("a")) return;
      exportSelected();
    });
  }

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
