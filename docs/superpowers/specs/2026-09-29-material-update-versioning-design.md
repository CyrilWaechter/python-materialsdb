# Design: Material update versioning — detect, report, opt-in update

Date: 2026-09-29
Status: Approved (brainstorming session)

## Goals

An IFC model holds a frozen copy of the material data pushed into it. Today a
re-push of a material whose producer updated its data (same GUID) is a silent
no-op, and a regenerated GUID leaves a stale duplicate behind.

This feature detects that a pushed material has changed upstream, reports the
change, and offers an explicit **opt-in** update or replacement. It never
changes material data on its own ("never silently auto-update").

The layers currently present in the model are the unit of work: we never add
layers the model was not using.

## Decisions

- **Fingerprint = content digest of the whole material definition.** `sha256`
  of the material's stored XML blob (`materialsdb.store` already keeps
  `sqlite3.Binary(etree.tostring(element))` per material). The digest is over
  the bytes as stored, so a pure reformatting counts as a change; it is stable
  across re-ingest of an unchanged file. Producers are expected to bump
  `ver`/`crd`; noise is a useful signal.
- **Versioned scheme marker.** Two properties are written into the identity
  pset: `fingerprint` (hex) and `fingerprint_scheme` (`"materialsdb-fp/1"`).
  On a scheme mismatch the comparison is `unknown`, not `changed`, so a future
  algorithm change does not mass-flag models.
- **Server computes, add-on stores.** Only the server can hash (it owns the
  store); the digest travels in the push payload. The add-on never hashes.
- **Per-material scope, layer-by-layer matching.** A change to any layer fires
  the material fingerprint; updating walks the model's layers and matches them
  against the new version.
- **Matching: identity first, then thickness.**
  - if the model layer's `materialsdb.org_layer.layer_id` still exists in the
    new version → that is the match (a stable id survives λ/thickness edits);
  - otherwise match by **thickness** (country-resolved; the model layer's
    matching thickness is its `org_layer.thick`, falling back to
    `IfcMaterialLayer.LayerThickness` when `org_layer` is absent);
  - model layer with **no thickness / 0** → matches only if the new version has
    **exactly one layer**; several layers there means thickness-specific
    variants were added → ambiguous (user warning), never guessed;
  - several thickness candidates → ambiguous → user picks;
  - no candidate → not updatable in place → replace or keep.
- **λ is shown, not matched.** Thickness is the match key; a λ change appears
  in the change report only.
- **Opt-in granularity: update, keep, replace** per material, and replace is
  available per unmatched layer and for the whole material.
- **Superseded entities are removed** from the model on replacement.
- **Change report = schedule of changed fields only** (old → new).
- **Surfaces:** the picker shows per-material model status before pushing and
  offers the actions; the add-on also reports not-opted-in changed materials at
  push time as a safety net.
- **`btk` materials** (variations) are **not updatable in place** for now
  (marked accordingly); plain layered materials only.

## Prerequisite: uniform material data across push flows (bug)

The picker flow and the construction flow do not currently produce the same
model data. Confirmed in code:

- `build_add_construction_payload` emits material entries **without `psets`**
  and without `materialsdb.org_layer` (the payload test asserts no `psets`).
- `apply_add_construction` writes only `_add_identity_pset` + `_apply_style`,
  never `_add_property_psets` (nor `org_layer`).

Result: construction-pushed materials carry only the `materialsdb` identity
pset, so they have no per-layer λ/geometry data and no layer id to match on.
Matching/updating would fail. This must be fixed first.

**Decision — attaching data to a construction layer.** A construction layer is
a materialsdb material at a **design thickness**. Resolve it to a materialsdb
layer as follows:
1. the material layer whose country-resolved thickness equals the design
   thickness;
2. else, if the material has exactly one layer, that layer;
3. else (multi-layer material, no thickness match) → attach the material's
   **first layer** as a best effort and surface a problem/warning to the user,
   rather than rejecting an existing construction. The material is then treated
   as not updatable in place.

`materialsdb.org_layer` always records the **resolved materialsdb layer**
(GUID and materialsdb thickness), so matching later never depends on the design
thickness. The model stores both identities:
`IfcMaterialLayer.LayerThickness` = design thickness,
`materialsdb.org_layer.thick` = materialsdb thickness.

Change: `build_add_construction_payload` includes `psets` (via
`_resolved_psets`) + `org_layer`; `apply_add_construction` writes identity +
`_add_property_psets` (+ `org_layer`, which is part of `psets`) for created
materials, and refreshes them for reused ones.

## Architecture

```
picker (app.js) ──GET /api/materials, /api/model/changes──▶ server ──store (fingerprints)──
                                                              ▲
add-on (Bonsai) ──POST /api/listener/model (map)──────────────┘
                 ◀── POST /api/listener/send (push, modes) ──
```

- The **server** computes store fingerprints, caches the model map, derives
  per-material status, and resolves push payloads (materials + decisions).
- The **add-on** writes/reads the identity pset, applies updates/replacements
  to the open model, and reports the model map.

## Data model

**Identity pset `materialsdb`** gains `fingerprint` and `fingerprint_scheme`
(written by `_add_identity_pset`; `MaterialBuilder._create_identity_pset`
gains them on the export paths too, for consistency).

**Store**: `MaterialStore.material_fingerprint(material_id) -> str | None`
(hex sha256 of the stored `xml` blob).

**Store schema**: add two tables to `_SCHEMA` (`__init__` runs
`executescript(_SCHEMA)` on every open, so they appear on existing databases
**without a schema bump or cache wipe**):

- `model_materials(model_path, material_id, fingerprint, scheme, layers TEXT,
  seen_at, PRIMARY KEY(model_path, material_id))` — the model map, where
  `layers` is a JSON list of `{layer_id, thick}` for the material's model
  layers (needed for matching/candidates).
- `pushed_materials(model_path, material_id, layer_id, snapshot TEXT,
  pushed_at, PRIMARY KEY(model_path, material_id, layer_id))` — the resolved
  entry the server sent at push time, kept so the change report can show
  **old → new**. It is best-effort: a push made from another machine/GUI has no
  snapshot, and the report then says "details unavailable" while still showing
  `changed`.

## Transport & caching

- **Add-on → GUI**: `POST /api/listener/model` (token-gated) with
  `{client_id, model_path, scheme, materials: {material_id: {fingerprint,
  scheme, layers: [{layer_id, thick}]}}}`. The add-on scans `IfcMaterial`
  identity psets, dedupes per `material_id`, and sends on `register` and after
  every push that touched materials. `thick` is the model layer's
  `materialsdb.org_layer.thick` when present, else the `IfcMaterialLayer`
  thickness, else null.
- **Cache**: rows in `model_materials` for `model_path`; the active model is
  the path the connected add-on last reported. `seen_at` is shown as "model
  state as of …" when the add-on is offline.
- **Push snapshot**: when the server builds a push payload it upserts the
  resolved entries into `pushed_materials` for the target model path (the diff
  basis).
- **Read endpoints** (local, not token-gated): material listings gain a
  `model` status per row; `GET /api/model/changes` returns counts by status,
  the list, and for each `changed` material the matching analysis (updatable /
  ambiguous with candidates / not updatable) plus the change report when a
  snapshot exists.

### Statuses

| Status | Meaning |
|---|---|
| `absent` | not in the model |
| `current` | in model, fingerprint == current store digest |
| `changed` | in model, fingerprint differs (same scheme) |
| `unknown` | in model, no fingerprint / different scheme (legacy baseline) |
| `gone` | in model, `material_id` no longer exists in the store |

## Update engine

**Payload decisions.** Each material entry gains `mode ∈ {skip, update, replace}`
(default `skip` for an existing material) plus `fingerprint` +
`fingerprint_scheme` in `identity`. The **server** owns matching: an `update`
entry carries the resolved mapping `update: {model_layer_id: new_layer_id}`
(model layer = `materialsdb.org_layer.layer_id`), and a `replace` entry carries
the **new** material's data plus a `replaces: {material_id, layer_id?}` target
identifying the model material/layer it supersedes.

**Applying an entry:**
- material absent from the model → create as today;
- present, fingerprint matches → no-op (also for `update`/`replace`);
- present, fingerprint differs:
  - `skip` → unchanged, added to the safety-net report (no backfill: a detected
    change must not be masked);
  - `update` → apply the mapping mechanically, refreshing only the listed model
    layers; if a mapped model layer is no longer present, report it and apply
    the rest (never guess);
  - `replace` → replace the targeted layer(s) or whole material.

**In-place update (per matched `IfcMaterial`, GUID and references preserved):**
1. identity pset: `material_id`/`company_id`/`company` + `fingerprint`/
   `fingerprint_scheme`;
2. `IfcMaterial` name/description/category (locale-resolved);
3. our `materialsdb.*` psets: remove and rewrite from the resolved payload
   (foreign psets untouched);
4. `IfcMaterialLayer`: thickness/name/description, and `materialsdb.org_layer`
   re-pointed to the matched new layer id + materialsdb thickness;
5. style via `_apply_style` (preserve third-party, refresh ours).

**Replace:** layer-level swaps one model layer's `IfcMaterial` for the chosen
material's data; whole-material swaps all of that material's model layers.
Superseded `IfcMaterial`, its layer, psets and style are removed
(`purge_material`-style cascade, including the `IfcMaterialLayerSet` layer
when the set becomes empty).

**Legacy baseline.** The first push that encounters a material with no
`fingerprint` writes the current digest (bookkeeping only, no warning) — a
one-time baseline; the next change is then detected. Trade-off: a change that
happened before the baseline is not detectable.

## Picker UI

- **Banner / summary** from `GET /api/model/changes`: counts of `changed`,
  `gone`, `unknown`.
- **Per-material status** in the list.
- **Change report (schedule)** on a `changed` material: only modified rows,
  old → new — name/description, category, colour, usage flags, and per matched
  layer the thickness and λ.
- **Actions**: `Keep` (default), `Update` (enabled only when unambiguously
  updatable), `Replace…` (choose another material; per unmatched layer or whole
  material).
- **Ambiguity**: list candidate new layers (thickness) to pick from.
- **Bulk**: "Update all changed" acts only on unambiguously updatable materials.
- **Push result**: includes "N changed materials were left unchanged (not opted
  in)".

## Error handling

- Per-material best-effort: one failing material does not abort the push; the
  add-on reports per-entry outcomes.
- Styling failures remain non-fatal (existing pattern).
- Unresolvable construction layers are rejected at payload build time with a
  problem message (prerequisite section).
- An offline add-on leaves the cached map served with `seen_at`.

## Testing

**Unit / CI (pure ifcopenshell + store):**
- fingerprint: stable across re-ingest of the same file; changes when a
  material's data changes; scheme mismatch → `unknown`.
- matching: id-first; thickness match; thick-less with one layer; thick-less
  with several (ambiguous); no match.
- update in place: preserves `IfcMaterial` GUID, updates our psets/layer/
  style, leaves foreign psets; `org_layer` re-pointed.
- replace: superseded entities removed; layer set cleaned when emptied.
- prerequisites: construction payload carries `psets` + `org_layer`; apply
  writes them for created and reused materials.
- endpoints: `/api/listener/model` ingestion (fingerprint + layers), the five
  statuses, `/api/model/changes` (incl. matching analysis), `model_materials`
  and `pushed_materials` population.
- change report: old → new from the push snapshot; "details unavailable" when
  there is no snapshot.
- legacy: no fingerprint → silent baseline; then a subsequent change is
  `changed`.

**In-Blender harness (local):** push → mutate the store material → re-push
with `update` → assert data updated, GUID preserved, model map re-sent;
`replace` removes the superseded material.

## Compatibility

- Pre-feature models: `unknown` until the first push backfills a baseline.
- Different fingerprint scheme: `unknown`.
- Add-on offline / no model: cached map (`seen_at`), no actions.
- Duplicate material ids across producers: the store keeps the first row; the
  fingerprint is that row's.

## Out of scope

- The prerequisite construction-data fix is required and ships first, as its
  own commit/plan before the feature.
- `btk`/variations in-place update.
- Any automatic (non-opt-in) update.
- Multi-model arbitration beyond "the connected model".
