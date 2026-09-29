# Construction Material Data Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Materials pushed from the construction maker carry the same resolved `materialsdb.*` property sets (and the layer GUID in `materialsdb.org_layer`) as materials pushed from the picker.

**Architecture:** Resolve each construction layer to a materialsdb layer in `build_add_construction_payload` (design thickness match → single-layer material → first layer with a warning), emit its resolved `psets`, and make `apply_add_construction` write/refresh those psets on the model's `IfcMaterial` entities.

**Tech Stack:** Python 3.14, lxml, ifcopenshell.

**Spec:** `docs/superpowers/specs/2026-09-29-material-update-versioning-design.md` (section "Prerequisite: uniform material data across push flows")

## Global Constraints

- src-layout: code under `src/materialsdb/`, add-on under `bonsai_addon/`, tests under `tests/`.
- Local test command: `python3 -m pytest -p no:pytest-blender -q` with `PYTHONPATH=src`.
- Lint/format/type gates: `ruff check --exclude src/materialsdb/classes.py src tests dev_utils bonsai_addon`, `ruff format --check .`, `ty check --project .`.
- `bonsai_addon/insert.py` stays pure ifcopenshell (no `materialsdb` import).
- Layer thickness unit in `materialsdb.org_layer.thick` is metres (`unit_factor` 0.001 in `material_psets.json`).

## Review Focus

- A multi-layer material whose design thickness matches no layer must not crash the payload build and must not silently mis-attach data (warn, best-effort first layer).
- A material with no layers at all (e.g. `simple` with an empty `layers` element) must not raise.
- Placeholder construction layers (no material) must keep their current shape.
- Reused construction materials (`known`) must get their psets refreshed, not only newly created ones.
- A layer with no country geometry/thickness must still produce `org_layer.layer_id` with `thick` absent/null.

---

### Task 1: Resolve a construction layer to a materialsdb layer and emit its psets

**Files:**
- Modify: `src/materialsdb/gui/listener.py` (`build_add_construction_payload`, new helper)
- Test: `tests/test_listener_payload.py`

**Interfaces:**
- Consumes: `utils.get_material_layers(material)`, `utils.get_by_country(values, country)`, `_resolved_psets(layer, country)`.
- Produces: `_resolve_construction_layer(material, design_thickness_m, country) -> tuple[object | None, str | None]` returning `(materialsdb_layer_or_None, warning_or_None)`; construction payload material entries gain `"psets"`; `payload["construction"]["warnings"]` is a `list[str]` (may be empty).

- [ ] **Step 1: Write the failing test**

In `tests/test_listener_payload.py` add:

```python
def test_construction_payload_carries_resolved_psets(store):
    payload, problems = build_add_construction_payload(
        store,
        body={"name": "w", "layers": [{"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2}]},
    )
    assert problems == []
    material = payload["construction"]["layers"][0]["material"]
    org = material["psets"]["materialsdb.org_layer"]
    assert org["layer_id"] == "00000000-0000-0000-0000-0000000000a1"  # the 200 mm layer
    assert org["thick"] == 0.2
    assert payload["construction"]["warnings"] == []


def test_construction_payload_warns_when_thickness_unmatched(store):
    # Isolant A has 200 mm and 100 mm layers; 0.15 m matches neither, and it has
    # more than one layer -> best effort first layer + a warning.
    payload, problems = build_add_construction_payload(
        store,
        body={"name": "w", "layers": [{"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.15}]},
    )
    assert problems == []
    assert payload["construction"]["warnings"]
    material = payload["construction"]["layers"][0]["material"]
    assert "materialsdb.org_layer" in material["psets"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py -k construction_payload_carries_resolved_psets -v`
Expected: FAIL (`KeyError: 'psets'`).

- [ ] **Step 3: Implement the helper and wire it into `build_add_construction_payload`**

Add module-level `_layer_thick_m(layer, country) -> float | None` (geometry thick / 1000) and `_resolve_construction_layer(material, design_thickness_m, country)`:
1. `layers = list(utils.get_material_layers(material))`; if empty → `(None, "no material layer")`.
2. exact match on `_layer_thick_m` within 1e-9 → `(layer, None)`.
3. else if `len(layers) == 1` → `(layers[0], None)`.
4. else → `(layers[0], f"{name}: design thickness {design_thickness_m} m matches no layer")`.

In `build_add_construction_payload`, for each non-placeholder layer: resolve, collect the warning, and set the material entry's `"psets": _resolved_psets(resolved_layer, country)` when resolved is not None (otherwise omit). Add `"warnings": warnings` to the construction dict.

- [ ] **Step 4: Run the file's tests**

Run: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py -q`
Expected: PASS. Note: `test_build_add_construction_payload_shape` asserts `"psets" not in first` — update that assertion to `"psets" in first`.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/listener.py tests/test_listener_payload.py
git commit -m "fix(listener): resolve construction layers to materialsdb psets"
```

---

### Task 2: Write construction psets onto the model materials

**Files:**
- Modify: `bonsai_addon/insert.py` (`apply_add_construction`)
- Test: `tests/test_bonsai_insert.py`

**Interfaces:**
- Consumes: `entry["psets"]` (Task 1) and `existing._add_property_psets(file, material, psets)`.
- Produces: construction-pushed `IfcMaterial` carries `materialsdb.org_layer` plus every resolved pset, for created and reused materials.

- [ ] **Step 1: Write the failing test**

Add psets to `CONSTRUCTION_PAYLOAD`'s Isolant A entry, e.g. `"psets": {"materialsdb.org_layer": {"layer_id": "...a1", "thick": 0.2}}`, then:

```python
def test_construction_writes_material_psets():
    file = ifcopenshell.file(schema="IFC4")
    apply_add_construction(file, CONSTRUCTION_PAYLOAD)
    isolant = next(m for m in file.by_type("IfcMaterial") if m.Name == "Isolant A")
    psets = [p for p in file.get_inverse(isolant) if p.is_a("IfcMaterialProperties")]
    org = [p for p in psets if p.Name == "materialsdb.org_layer"]
    assert len(org) == 1
    props = {prop.Name: prop.NominalValue.wrappedValue for prop in org[0].Properties}
    assert props["layer_id"] == "00000000-0000-0000-0000-0000000000a1"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -k construction_writes_material_psets -v`
Expected: FAIL (`len(org) == 0`).

- [ ] **Step 3: Implement**

In `apply_add_construction`, after `_add_identity_pset(file, material, entry["identity"])` for a created material, add `_add_property_psets(file, material, entry.get("psets"))`. For a reused material (`material = known.get(...)`), call `_add_property_psets` too (idempotent via the feature's later update path; for now just add it on create and on reuse when the material has no `materialsdb.org_layer` pset).

- [ ] **Step 4: Run the file**

Run: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bonsai_addon/insert.py tests/test_bonsai_insert.py
git commit -m "fix(bonsai): write resolved psets for construction-pushed materials"
```

---

### Task 3: Full gate

- [ ] **Step 1: Run all gates**

```bash
ruff check --exclude src/materialsdb/classes.py src tests dev_utils bonsai_addon
ruff format --check .
ty check --project .
PYTHONPATH=src python3 -m pytest -p no:pytest-blender -q
```
Expected: all pass.

- [ ] **Step 2: Commit any fixes** (if the gates required changes).
