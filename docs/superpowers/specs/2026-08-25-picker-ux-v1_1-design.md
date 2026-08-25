# Design: Material picker UX v1.1 (dense grid + stack drawer)

Date: 2026-08-25
Status: Approved direction (visual brainstorm: Direction A dense grid, bottom-drawer preview)

## Goals

Fix v1's rough edges and complete the picker's interaction model:

1. Name column shows the material name in the selected language (bugfix — cell
   was never wired).
2. Detail pane becomes readable formatted key/value content (no raw JSON).
3. Search updates live while typing (debounced), no button click.
4. Controls become self-explanatory; per-column facet filters with checkboxes;
   usage shown as translated words and filterable.
5. Materials with multiple layers expand inline (▸ arrow); the user picks ALL
   layers or a specific layer for append/export.
6. Optional scaled stack preview in a bottom drawer (btk/construction get
   honest alternatives).

Non-goals: construction maker (future sub-project), UI-chrome i18n (data stays
localized; interface labels remain English), pagination.

## Decisions from the visual session

- Layout: Direction A — one dense sortable table, detail strip below,
  filter chevrons on column headers.
- Stack preview: collapsible **bottom drawer** spanning the table width;
  horizontal bars scaled by thickness; layer checkboxes mirror the table
  expansion; opens only via an explicit "preview" affordance (never auto-open),
  collapses back down.

## Frontend changes (`static/app.js`, `index.html`)

- **Name resolution**: rows render `display_name` (server-resolved, see API)
  with `names` fallback client-side; column header "name".
- **Live search**: input debounced 250 ms re-filters the client-side table
  model (no server round-trip per keystroke).
- **Client-side table model**: fetch the full summary list once per
  language/config change (~2.3k compact rows); sort/filter/facet locally —
  instant, offline-friendly. Server keeps serving the same filtered endpoint
  for plugin/API consumers.
- **Facet dropdowns**: chevron under each of company / type / category /
  usage headers opens a checkbox list with live counts (computed from loaded
  data). Multiple facets combine (AND across facets, OR within a facet).
- **Usage**: rendered as localized plain words ("Mur Toit" not "WRT"); usage
  facet lists the four flags translated via a small built-in dictionary
  (en/fr/de) — data itself remains boolean flags.
- **Sort controls**: clickable column headers replace the "sort select";
  explicit labeled order toggle (↑/↓) replaces the "desc" checkbox; the type
  filter moves into its column facet. Toolbar keeps only: search box,
  language/country selects, action buttons.
- **Detail pane**: definition-list layout — localized name(s) per lang as
  sub-lines, description paragraph, then metrics grouped (thermal / geometry /
  usage chips / identity). btk adds U-value lines; construction shows consref +
  designusage. No JSON anywhere.
- **Layer expansion**: materials with layers render an expander row; child
  rows list source layer id (short form), thickness, λ, with a checkbox each;
  the parent checkbox toggles all layers. Selection granularity:
  - no expansion / parent checked → whole material (current behavior)
  - specific layers checked → only those layers are appended/exported
- **Bottom drawer**: "preview" affordance per row (and auto-open on expand?)
  opens the drawer rendering the layer stack to scale (flex widths ∝ mm),
  labels = short id + thickness; checkboxes mirrored with the table expansion.
  Type-aware fallbacks: btk → variation list (thickness + U-value per
  variation) instead of a stack (variations are alternatives, not a stack);
  construction → consref/designusage text.

## Backend changes

### summary payload gains display fields

`store.summaries()` already resolves per-lang names internally for sorting;
extend the GUI contract without breaking it:

- New lightweight endpoint semantics in `gui/server.py`: `/api/materials`
  response rows gain `display_name` (resolved via configured/requested lang,
  fallback "" entry) so the frontend never ships all languages per row.
  Full `names` dict remains available on the detail endpoint only.
- Detail endpoint (`/api/materials/{id}`) additionally returns a `layers`
  array: `[{id, thick, lambda_value}]` (country-resolved, source guids
  included) powering both the table expansion and the drawer.

### material_builder: partial-layer creation

- `MaterialBuilder.build(material, ..., layer_ids=None)` — `layer_ids=None`
  keeps current behavior (all layers); a set/list of source layer guids
  restricts creation to matching layers (matched by `layer.id`).
- `add_material(..., layer_ids=None)` passthrough.
- **Layer identity persistence**: every created IfcMaterialLayer gets
  `Description=str(source_layer.id)` so host plugins and future tools can map
  IFC layers back to materialsdb layers (IfcMaterialLayer has no GlobalId).
- Identity/idempotency semantics unchanged: identity is still material-level;
  appending a different subset of the same material with `replace=True`
  replaces the whole previous set (documented behavior).

### README

Add the run-from-source alternative under the GUI section:
`PYTHONPATH=src python3 -m materialsdb.gui`.

## Error handling & edge cases

- Layer selection on layerless materials (construction/btk): expansion row
  absent; pick acts on whole material; drawer shows the type-specific fallback.
- Empty facet state ("no matches") renders an explicit empty-state row.
- Stale selection after language switch: selection keys are ids → survives.

## Testing

- Backend: `build(layer_ids=[...])` unit tests (subset created, others absent,
  Description carries source guid); detail-endpoint layers array test;
  display_name presence test.
- Frontend: untested by automation (unchanged spec limitation); manual
  checklist recorded in the plan (name shows in lang, live search, facets,
  expansion, drawer, per-layer append verified by reopening the saved .ifc and
  checking IfcMaterial count/names).
