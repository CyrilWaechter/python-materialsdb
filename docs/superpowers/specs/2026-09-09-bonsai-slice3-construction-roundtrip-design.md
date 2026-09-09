# Design: Bonsai slice 3 — construction round-trip (Bonsai ⇄ composer)

Date: 2026-09-09
Status: Approved (brainstorming session)
Base spec: `2026-08-27-bonsai-integration-transport-design.md` (transport), `2026-09-08-bonsai-slice2-construction-push-design.md` (construction push + undo)

## Goals

Round-trip a construction between Bonsai and the composer: push an existing
type's `IfcMaterialLayerSet` into the composer (incoming queue), edit it
(thicknesses, layer order, materials), and push it back — slice 2's upsert
modifies the same layer set in place.

## Decisions

- **Selection: active object, type or occurrence.** The panel operator reads
  the active object; `tool.Ifc.get_entity(obj)` resolves the element. A
  `IfcTypeProduct` is used directly; an occurrence resolves via
  `ifcopenshell.util.element.get_type(element)` (None → explicit error).
- **Utils over manual traversal** (maintainer rule): reads use
  `ifcopenshell.util.element.get_type/get_material/get_psets`. Error handling
  contract for `get_material` — it returns ANY `IfcMaterialDefinition`:
  `None` → "type has no material"; `IfcMaterialLayerSet` → usable; any other
  class (`IfcMaterial`, `IfcMaterialList`, `IfcMaterialConstituentSet`,
  `IfcMaterialProfileSet`, `IfcMaterialLayerSetUsage`, …) → error naming the
  actual class ("material is not an IfcMaterialLayerSet: IfcMaterialList").
  Layers without a Material become nameless placeholders (never skipped);
  layers with missing/zero `LayerThickness` are included as thickness 0 (the
  composer already flags invalid thickness; save enforces > 0).
- **Placeholder materials are first-class and keepable (same-model).** A
  layer whose material carries no `materialsdb` identity pset travels as
  `{material_id: null, placeholder: {name, lambda_value}}` — name from
  `IfcMaterial.Name`, λ from `get_psets(material)["Pset_MaterialThermal"]
  ["ThermalConductivity"]` (None when absent). The composer shows them with a
  "model material" badge (editable thickness, λ read-only, no material
  dropdown); they persist in the construction JSON; U-value uses the
  recorded λ. Cross-project adoption via a project-specific materialsdb
  producer file is a follow-up slice.
- **Push-back re-attaches by name.** `apply_add_construction`'s placeholder
  branch finds the model's `IfcMaterial` by `Name == placeholder.name` and
  reuses it; if absent it creates it **with its thermal pset** (λ persists in
  the model). No duplicate materials on same-model round-trips.
- **design_usage from the selected class**: IfcWallType/IfcWall →
  consDesignForWall, IfcSlabType/IfcSlab → consDesignForFloor,
  IfcRoofType/IfcRoof → consDesignForRoof, anything else → generic.
- **Incoming queue**: in-memory list on `GuiState` (newest first, cap 20).
  `POST /api/composer/push` (token) prepends after minimal validation (name
  required, layers non-empty, thickness finite/≥ 0 — save still enforces >
  0); `GET /api/composer/incoming` lists; `POST /api/composer/incoming/consume`
  `{index}` removes on edit-copy. GUI polls every 2s and renders an
  "incoming from model" section.
- **Standalone export rejects placeholders**: `to_ifc_layer_set` raises
  "cannot export placeholder layers; assign materialsdb materials first" —
  explicit, never silent.

## Components

- `bonsai_addon/read_construction.py` (NEW, pure ifcopenshell + utils,
  CI-testable): `read_construction_from_element(element) -> dict | None`
  with `reason` surfaced via a `ReadError(ValueError)` carrying the message;
  returns `{"name", "design_usage", "layers": [{material_id?, thickness_m,
  placeholder?}]}`.
- `bonsai_addon/__init__.py`: `MATERIALSDB_OT_send_construction` (plain
  operator, read-only — no `Ifc.Operator` base, no undo step): active object
  → read → `POST /api/composer/push`; Blender status report for outcomes
  ("sent N layers (M model material)" / errors). Panel button under the
  toggle.
- `bonsai_addon/insert.py`: read-refactor to utils (`get_psets` per
  `IfcMaterial` replaces manual `IfcMaterialProperties` iteration in
  `existing_materials_by_id`/`_existing_keys` — idempotence semantics
  unchanged); push-back placeholder branch (name-match via the same util
  reads; create-with-thermal-pset when absent); `apply_add_construction`
  summary gains `placeholders_matched` (detail string extended).
- `src/materialsdb/construction.py`: `ConstructionLayer.material_id` nullable
  + `placeholder: dict | None`; `validate_construction` accepts
  (resolvable material_id) OR (null id + placeholder dict); save/load JSON
  round-trips placeholders; `u_value` uses placeholder λ; `to_ifc_layer_set`
  rejects placeholders.
- `src/materialsdb/gui/server.py`: push/list/consume endpoints (token-gated);
  `build_add_construction_payload` passes placeholder layers through as
  `{material_id: null, thickness_m, placeholder}` (no `material` dict).
- `src/materialsdb/gui/static/app-constructions.js` + `constructions.html`:
  incoming section (polled), consume-and-load via `loadEditable`, placeholder
  layer rows (badge, λ display, no dropdown), save/send include placeholders.

## Error handling (read path)

| Condition | Behaviour |
|---|---|
| no active object / element unlinked | Blender error report "select an IFC element" |
| occurrence with `get_type() == None` | "selected element has no type" |
| type with `get_material() == None` | "type has no material" |
| `get_material()` returns non-layer-set `IfcMaterialDefinition` | "material is not an IfcMaterialLayerSet: <class>" |
| layer `Material is None` | placeholder, name "", λ None |
| `LayerThickness` None/0 | included with thickness 0 (composer flags; save enforces > 0) |
| server push with empty name / empty layers | 400 mirroring existing validation style |

## Testing

- CI: `read_construction_from_element` graph fixture (occurrence+type, layer
  set with resolvable + foreign + thermal-pset + material-less layers;
  wrong-material-class type → ReadError); construction placeholder
  validate/save/load/u_value/export-rejection; server push/list/consume +
  placeholder pass-through; `apply_add_construction` placeholder branch
  (reuse-by-name, create-with-λ, `placeholders_matched`); Node harness:
  incoming section + placeholder row rendering.
- In-Blender harness: full round-trip — panel-op push (occurrence selected)
  → incoming → consume → modified push-back → same set entity updated,
  placeholder material re-attached by name (no duplicate `IfcMaterial`),
  type object still outliner-linked.
- Manual: composer placeholder UX checklist.

## Non-goals

Cross-project local producer file / identity stamping (follow-up); editing
placeholder λ in the composer (read-only display; write-back noted as
future); incoming pushes from non-Bonsai listeners (protocol stays
host-agnostic).
