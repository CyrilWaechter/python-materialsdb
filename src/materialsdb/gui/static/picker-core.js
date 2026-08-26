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

  return { CATEGORY_COLORS, decimalToHex, categoryColorStyle, esc };
})();
