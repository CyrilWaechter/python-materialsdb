# Design: Bonsai slice 2 — construction push + undoable, api-based insertion

Date: 2026-09-08
Status: Approved (brainstorming session)
Base spec: `2026-08-27-bonsai-integration-transport-design.md` (transport + slice 1)

## Goals

1. **Construction push**: send a construction from the construction maker into
   the active Bonsai model — the matching type element per `design_usage`
   (wall → `IfcWallType`, floor → `IfcSlabType`, roof → `IfcRoofType`,
   generic/none → all three) sharing one `IfcMaterialLayerSet`.
2. **Undo**: material and construction pushes must be revertible with Blender
   Ctrl+Z through Bonsai's IFC undo system.
3. **Rework slice-1 insertion on `ifcopenshell.api`** — canonical entity
   creation (auto `GlobalId`/`OwnerHistory`, schema-valid material styling)
   instead of raw `create_entity`.

## Decisions

- **Upsert by name, modify the set in place**: the type element is created if
  missing and kept if present; a re-send rebuilds its existing
  `IfcMaterialLayerSet` (same entity: old `IfcMaterialLayer`s removed, new
  ones built, `LayerSetName` synced). Slice 3 (round-trip edit) builds on
  this. Documented caveat: a foreign same-named type gets its material
  replaced — the user sent that construction name intentionally.
- **Generic usage shares one set**: one `IfcMaterialLayerSet` +
  one `IfcRelAssociatesMaterial` with `RelatedObjects=[wall, slab, roof]`
  (`material.assign_material(products=[...])` handles this natively).
- **Self-sufficient payload**: referenced materials are found by their
  `materialsdb` identity pset or created on the fly (minimal
  `to_ifc_layer_set`-style: identity pset + guarded material style; no
  per-layer psets).
- **api-based insert**: `root.create_entity` (auto-GlobalId — raw
  `create_entity` does NOT generate one), `material.add_material`,
  `material.add_material_set`, `material.add_layer` + `material.edit_layer`
  (api's `add_layer` takes no thickness), `material.assign_material`,
  `pset.add_pset`/`edit_pset` (custom pset names: fall back to raw
  `IfcMaterialProperties` creation if api rejects them — decided in CI),
  `style.add_style`/`add_surface_style`/`assign_material_style` (needs a
  representation context — best-effort, never fatal). Known drift trap:
  `class` → `ifc_class` kwarg rename between ifcopenshell 0.8/0.9 — the
  in-Blender harness runs the real bonsai-bundled ifcopenshell, so drift
  fails loudly at test time.
- **Undo via a real operator**: new
  `MATERIALSDB_OT_apply_push(bpy.types.Operator, tool.Ifc.Operator)` with
  `bl_options = {"REGISTER", "UNDO"}` and `_execute` dispatching on the
  pending push's action. The base class's final `execute()` wraps it in
  `IfcStore.execute_ifc_operator` (IFC begin/end transaction + rollback/
  commit callbacks + UI refresh); Blender pushes the undo step and
  `undo_post` maps it to `IfcStore.undo` via the transaction key. The timer
  hands the payload over via a module-global `_PENDING` and invokes
  `bpy.ops.materialsdb.apply_push()`. Slice-1 material pushes ride the same
  operator, so they become undoable too.

## Payload (server-resolved)

```json
{"action": "add_construction", "construction": {
  "name": "Mur 20+16", "design_usage": "consDesignForWall",
  "types": ["IfcWallType"],
  "layers": [{"material_id": "…", "thickness_m": 0.2,
              "material": {"source_id": "…", "name": "Isolant A",
                           "description": "…", "category": "Insulation",
                           "color": 16711680,
                           "identity": {"material_id": "…",
                                        "company_id": "…", "company": "…"}}}]}}
```

`types` resolved server-side from `design_usage`. `thickness_m` is the
CONSTRUCTION thickness (user-set in the composer), not a manufacturer value.

## Server

- `gui/listener.py`: `build_add_construction_payload(store_, body)` —
  reuses `construction.validate_construction`, maps `design_usage` → types,
  resolves each layer's material (identity/name/category/color from
  summary + material).
- `_listener_send` routes `action == "add_construction"` (400 with
  validation problems otherwise, mirroring the existing `add_materials`
  guard).

## Add-on

- `insert.py`: rework `apply_add_materials` on api; new
  `apply_add_construction(file, payload) -> summary` — find-or-create layer
  materials, build layers (api `add_layer` + `edit_layer` thickness), then
  upsert per target types: if any target already has a layer-set relation,
  rebuild THAT set in place and consolidate all targets onto it (dropping
  sets left unreferenced); else `add_material_set` + one `assign_material`
  for all targets; missing types via `root.create_entity`. Returns
  `{"types_created", "sets_updated", "materials_created"}` for the status
  detail.
- `__init__.py`: `MATERIALSDB_OT_apply_push` operator (undoable); the poll
  timer sets `_PENDING` and calls `bpy.ops.materialsdb.apply_push()`.

## GUI

- `picker-core.js`: `startTargetPoll(api, selectEl, buttonEl)` — the
  clients-poll/target-dropdown wiring extracted from the picker page; the
  picker refactored to use it; constructions page gains the same control
  plus a "send to Bonsai" button posting
  `{client_id, action: "add_construction", construction: {...}}`.

## Testing

- CI: insert.py unit tests reworked on api (entity counts, identity pset
  values, GlobalId present on types); construction upsert tests — same set
  entity (same GlobalId) modified on re-send, no duplicate types, generic →
  3 types + 1 shared set, find-or-create reuses materials added by
  `apply_add_materials`; server payload-resolution tests (types mapping,
  thickness override, validation failures); picker target-poll helper test
  (Node, pure logic).
- In-Blender harness: `test_construction_roundtrip` (server subprocess →
  send → `_poll_timer`/operator → `tool.Ifc.get()` assertions), plus an
  **undo test** — `bpy.ops.ed.undo()` removes inserted materials/types,
  `redo()` restores them.
- Manual: constructions page UX checklist (target dropdown, status marks).

## Non-goals

`IfcMaterialLayerSetUsage`, geometry placement, slice-3 (edit existing
construction from Bonsai), localization of add-on UI.
