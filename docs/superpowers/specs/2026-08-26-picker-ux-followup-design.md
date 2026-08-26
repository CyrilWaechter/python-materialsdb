# Design: Picker UX Follow-up — Preview Colors, Mini-Picker & Tabs

Date: 2026-08-26
Status: Approved (v1.1 follow-up, branch feat/material-picker-gui continues)
Source: Feedback round 2 (bugs + 3 feature requests)

## Goals

Polish the in-progress construction maker + picker UX so that:

1. The construction preview distinguishes layers by color/boundary. Pattern: material's own XML `information.color` when present, otherwise `CATEGORIES[material.information.group].color`.
2. The "Add layer" chooser reuses the material picker's table logic to add **1+ materials** (whole-material) or **specific layers** (a manufacturer's offered thickness variant).
3. Navigation between the two tools is obvious: a shared top tab bar `[ Materials | Constructions ]`.

## Decisions

- **Color rule:** Own XML color (decimal → `#RRGGBB` via existing `color_xml_to_ifc` logic, adapted to CSS) takes precedence; fallback is `CATEGORIES[category].color`. Boundaries are `2px solid #fff` between stack segments; the material's table row also gets a matching left-border tint for cross-reference. No new deps.

- **Mini-picker reuse:** Extract shared table engine into `static/picker-core.js` — pure functions: `fetchMaterials(q)`, `sortMaterials(list, key, dir)`, `filterByFacets(list, selections)`, `renderTable(container, rows, {multiSelect, expandLayers})`. Both `index.html` (full picker) and the constructions chooser modal import it. Single source of truth; vanilla JS, no build step.

  Alternatives considered: copy-pasting the table (divergence) and iframing the picker (nested scroll, postMessage) — rejected.

- **Tabs:** Duplicated markup in both HTML files (no templating): `<nav class="top-tabs"><a href="/">Materials</a><a href="/constructions.html">Constructions</a></nav>` with `.active` styling. Removes the low-visibility footer link.

## Scope

**In:**
- Preview segment colors + segment boundaries
- `picker-core.js` extraction + wiring into both pages
- Chooser modal: multi-select checkboxes (material + per-layer when expanded), "Add N selected" button
- Tab bar on both pages

**Out:**
- Fixing `thick` exposure in `materialsdb.org_layer` (already landed)
- Any new server endpoints — existing `/api/materials`, detail, and `/api/materials/legacy/*` cover the chooser's needs
- JS automation beyond the existing harness smoke test + `node --check`

## Verification

- `PYTHONPATH=src python3 -m materialsdb.gui` → picker tab shows colors, constructions preview shows colored segments
- Headless harness: add 2 materials via chooser (one whole, one specific layer thickness) → `layers` array length and `thickness_m` match
- Existing `python3 -m pytest -p no:pytest-blender -q` green

## Risks

- `picker-core.js` touches the working picker page — guarded by keeping its behavior identical (existing harness still passes).
- Colors for materials lacking both own color and category (fallback `Others` white) may be low-contrast; acceptable — preview segment text remains readable on it.
