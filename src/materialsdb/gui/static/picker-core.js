/* picker-core.js — shared table engine for picker pages
 * Vanilla JS, no build step. Exposes window.PickerCore for both
 * index.html and constructions chooser modal.
 */
const PickerCore = (() => {
  const CATEGORY_COLORS = {
    Others: [255, 255, 255],
    Water_Proof: [255, 255, 255],
    Vapour_Proof: [0, 0, 0],
    Concrete: [0, 255, 0],
    Wood_Timberproducts: [91, 60, 17],
    Insulation: [253, 108, 158],
    Masonry: [253, 70, 38],
    Metal: [119, 181, 254],
    Mortar: [102, 0, 153],
    Plastics: [96, 96, 96],
    Stone: [0, 0, 255],
    Composite: [112, 141, 35],
    Films: [0, 0, 0],
    Render: [0, 0, 0],
    Covering: [0, 0, 0],
    Glas: [27, 79, 8],
    Soil: [142, 84, 52],
  };

  function decimalToHex(decimal) {
    if (decimal == null) return null;
    const r = (decimal >> 16) & 255;
    const g = (decimal >> 8) & 255;
    const b = decimal & 255;
    return `rgb(${r},${g},${b})`;
  }

  function categoryColorStyle(category, ownColorDecimal) {
    const own = decimalToHex(ownColorDecimal);
    if (own) return own;
    const rgb = CATEGORY_COLORS[category] || CATEGORY_COLORS.Others;
    return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
  }

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function collectItems(selected, layerSelections) {
    /* Union of whole-material picks (`selected`) and layer picks
     * (`layerSelections`: materialId -> Set(layerGuid)). An id present in
     * both yields its layers (layer selection wins); each material keeps
     * ALL its selected layers in one {id, layer_ids} entry — the server
     * expands them into one material per layer/thickness. The embed-mode
     * composer proxy implements the same union with a per-layer
     * {material_id, thickness_m} shape instead. */
    const items = [];
    const seen = new Set();
    for (const id of selected) {
      seen.add(id);
      const layers = layerSelections.get(id);
      items.push(layers && layers.size ? { id, layer_ids: [...layers] } : { id });
    }
    for (const [id, layers] of layerSelections) {
      if (seen.has(id) || !layers.size) continue;
      items.push({ id, layer_ids: [...layers] });
    }
    return items;
  }

  function startTargetPoll(apiFn, select, button) {
    let clients = [];
    const labelOf = (c) => (c.model_path ? c.model_path.split(/[\\/]/).pop() : "unnamed");
    const refresh = async () => {
      try {
        clients = (await apiFn("/api/listener/clients")).clients;
      } catch {
        clients = [];
      }
      select.style.display = clients.length ? "inline" : "none";
      if (button) button.disabled = !clients.length;
      const previous = select.value;
      select.innerHTML = clients.map((c) => {
        const status = c.last_status ? c.last_status.status : "";
        const mark = status === "applied" ? " \u2713" : status === "error" ? " \u2717" : status === "pending" ? " \u2026" : "";
        const detail = c.last_status && c.last_status.detail ? ` title="${esc(c.last_status.detail)}"` : "";
        return `<option value="${esc(c.client_id)}"${detail}>${esc(labelOf(c))}${mark}</option>`;
      }).join("");
      if (clients.some((c) => c.client_id === previous)) select.value = previous;
    };
    refresh();
    const timer = setInterval(refresh, 2000);
    return {
      refresh,
      stop: () => clearInterval(timer),
      current: () => select.value || (clients[0] ? clients[0].client_id : null) || null,
      label: () => {
        const current = clients.find((c) => c.client_id === select.value) || clients[0];
        return current ? labelOf(current) : null;
      },
    };
  }

  function flash(setFn, getFn, text, ms = 5000) {
    /* Show a status message and clear it shortly after, so a new push is
     * visibly confirmed instead of blending into the previous message. */
    setFn(text);
    setTimeout(() => {
      if (getFn() === text) setFn("");
    }, ms);
  }

  return { CATEGORY_COLORS, decimalToHex, categoryColorStyle, esc, collectItems, startTargetPoll, flash };
})();
