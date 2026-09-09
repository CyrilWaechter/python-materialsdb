# Bonsai Slice 3 — Construction Round-Trip (Bonsai ⇄ Composer) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push an existing Bonsai type's `IfcMaterialLayerSet` into the composer (incoming queue), edit it — including "model material" placeholder layers for foreign materials — and push it back via slice 2's upsert.

**Architecture:** A pure-ifcopenshell reader (`bonsai_addon/read_construction.py`, utils-only per the maintainer rule) feeds a token-gated incoming queue on the GUI server; the composer lists incoming pushes, loads them as editable copies, and its save/send flows carry placeholder layers through the construction model back to the add-on, which re-attaches model materials by name.

**Tech Stack:** `ifcopenshell.util.element` (`get_type`/`get_material`/`get_psets`), stdlib server, vanilla JS, pytest-blender harness (existing).

## Global Constraints

- **Utils over manual traversal**: reads use `ifcopenshell.util.element.get_type/get_material/get_psets`. `get_material()` returns ANY `IfcMaterialDefinition` — handle `None` ("type has no material"), `IfcMaterialLayerSet` (usable), any other class (error naming the actual class). Never assume a layer-set.
- Placeholders: `{material_id: null, thickness_m, placeholder: {name: str, lambda_value: float | null}}`. Layers without a Material become nameless placeholders; missing/zero `LayerThickness` → thickness 0 (composer flags; save enforces > 0; incoming-push endpoint enforces ≥ 0).
- `bonsai_addon/read_construction.py` imports ONLY ifcopenshell(+utils) — no bpy/bonsai; `insert.py` likewise (existing rule).
- Server stays stdlib-only; all new endpoints token-gated; incoming queue capped at 20, newest first.
- ruff line-length 120; main suite `python3 -m pytest -p no:pytest-blender -q`; harness `pytest bonsai_addon/test -q` (pytest-blender ACTIVE); ty blind to `bonsai_addon/`.
- Untracked scratch files at repo root are NEVER added/fixed.
- `ConstructionLayer("0000…", thickness_m=…)` positional construction must keep working (material_id stays the first field).

---

### Task 1: Construction model — placeholders

**Files:**
- Modify: `src/materialsdb/construction.py`
- Test: `tests/test_construction.py`

**Interfaces:**
- Consumes: existing `Construction`, `ConstructionLayer`, `validate_construction`, `u_value`, `save/load_construction`, `to_ifc_layer_set`.
- Produces: `ConstructionLayer(material_id: str | None, thickness_m: float, placeholder: dict | None = None)`; `finite_or_none(value) -> float | None` (module-level, used by server Task 2); placeholder-aware validate/u_value/save/load; `to_ifc_layer_set` raises on placeholders.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_construction.py`:

```python
def _placeholder_construction():
    return Construction(
        name="Mixed wall",
        design_usage="consDesignForWall",
        layers=[
            ConstructionLayer("00000000-0000-0000-0000-000000000001", thickness_m=0.2),
            ConstructionLayer(
                None,
                thickness_m=0.18,
                placeholder={"name": "Brique terrecuite", "lambda_value": 0.21},
            ),
        ],
    )


def test_validate_accepts_placeholder_layers(store):
    from materialsdb.construction import validate_construction

    construction, problems = validate_construction(
        {
            "name": "Mixed wall",
            "design_usage": "consDesignForWall",
            "layers": [
                {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2},
                {"material_id": None, "thickness_m": 0.18, "placeholder": {"name": "Brique", "lambda_value": 0.21}},
                {"material_id": None, "thickness_m": 0.1, "placeholder": {"name": "", "lambda_value": None}},
            ],
        },
        store_,
    )

    assert problems == []
    assert construction.layers[1].placeholder == {"name": "Brique", "lambda_value": 0.21}
    assert construction.layers[2].placeholder == {"name": "", "lambda_value": None}


def test_validate_rejects_layer_without_material_nor_placeholder(store):
    from materialsdb.construction import validate_construction

    construction, problems = validate_construction(
        {
            "name": "x",
            "layers": [{"material_id": None, "thickness_m": 0.1}],
        },
        store_,
    )

    assert construction.layers == []
    assert any("material_id or placeholder required" in problem for problem in problems)


def test_u_value_uses_placeholder_lambda(store):
    construction = _placeholder_construction()

    result = u_value(construction, store_)

    assert result.u is not None
    contributions = result.contributions
    assert contributions[0]["material_id"] == "00000000-0000-0000-0000-000000000001"
    assert contributions[1]["name"] == "Brique terrecuite"
    assert contributions[1]["lambda_value"] == 0.21
    assert result.missing_lambda_ids == []


def test_u_value_flags_placeholder_without_lambda(store):
    construction = _placeholder_construction()
    construction.layers[1].placeholder = {"name": "Mystere", "lambda_value": None}

    result = u_value(construction, store_)

    assert result.u is None
    assert result.missing_lambda_ids == [None]


def test_save_load_roundtrips_placeholders(store, tmp_path):
    from materialsdb.construction import load_construction, save_construction

    save_construction(_placeholder_construction(), store_)

    loaded = load_construction("Mixed wall", store_)
    assert loaded is not None
    assert loaded.layers[1].material_id is None
    assert loaded.layers[1].placeholder == {"name": "Brique terrecuite", "lambda_value": 0.21}


def test_to_ifc_layer_set_rejects_placeholders(store):
    import pytest

    from materialsdb.construction import to_ifc_layer_set

    with pytest.raises(ValueError, match="cannot export placeholder layers"):
        to_ifc_layer_set(_placeholder_construction(), store_)
```

NOTE: `test_u_value_flags_placeholder_without_lambda` expects `missing_lambda_ids == [None]` — the placeholder layer contributes its `material_id` (None) to the missing list, consistent with the existing per-layer flagging contract. If the existing `u_value` missing-list semantics make this awkward, flag it in the report rather than silently changing the contract.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_construction.py -q`
Expected: FAIL — `TypeError: ConstructionLayer(None, ...)` (material_id typed `str`) and the new validate/u_value branches missing.

- [ ] **Step 3: Implement**

In `src/materialsdb/construction.py`:

Replace the `ConstructionLayer` dataclass:

```python
@dataclass
class ConstructionLayer:
    material_id: str | None
    thickness_m: float
    # {"name": str, "lambda_value": float | None} for model materials without
    # a materialsdb identity (round-tripped from Bonsai, re-attached by name)
    placeholder: dict | None = None
```

Add below the imports (before `RESISTANCE_PRESETS`):

```python
def finite_or_none(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
```

In `u_value`, replace the name/lambda resolution block at the top of the layer loop with:

```python
    for layer in construction.layers:
        if layer.placeholder is not None:
            name = layer.placeholder.get("name") or ""
            lambda_value = finite_or_none(layer.placeholder.get("lambda_value"))
        else:
            summary = store_.get_summary(layer.material_id)
            if summary is not None:
                name = summary.names.get(config.get_lang()) or summary.names.get("") or ""
            lambda_value = resolve_lambda(store_, layer.material_id, country)
```

(keep the rest of the loop — the missing/zero-thickness check, r_sum, contributions — unchanged; the missing list appends `layer.material_id`, which is None for placeholders.)

In `validate_construction`, replace the per-layer material resolution with:

```python
        material_id = entry.get("material_id")
        placeholder = entry.get("placeholder") if isinstance(entry.get("placeholder"), dict) else None
        if material_id:
            if store_.get(str(material_id)) is None:
                problems.append(f"unknown material id: {material_id}")
                continue
            layers.append(ConstructionLayer(material_id=str(material_id), thickness_m=thickness))
        elif placeholder is not None:
            layers.append(
                ConstructionLayer(
                    material_id=None,
                    thickness_m=thickness,
                    placeholder={
                        "name": str(placeholder.get("name") or ""),
                        "lambda_value": finite_or_none(placeholder.get("lambda_value")),
                    },
                )
            )
        else:
            problems.append(f"layer {index}: material_id or placeholder required")
```

In `save_construction` and `_to_body`, the layers serialization becomes:

```python
        "layers": [
            {
                "material_id": layer.material_id,
                "thickness_m": layer.thickness_m,
                **({"placeholder": layer.placeholder} if layer.placeholder else {}),
            }
            for layer in construction.layers
        ],
```

In `load_construction`, replace the layers parsing with:

```python
            layers = []
            for entry in data.get("layers", []):
                placeholder = entry.get("placeholder") if isinstance(entry.get("placeholder"), dict) else None
                layers.append(
                    ConstructionLayer(
                        material_id=entry.get("material_id"),
                        thickness_m=float(entry["thickness_m"]),
                        placeholder=(
                            {
                                "name": str(placeholder.get("name") or ""),
                                "lambda_value": finite_or_none(placeholder.get("lambda_value")),
                            }
                            if placeholder
                            else None
                        ),
                    )
                )
```

In `to_ifc_layer_set`, insert right after the docstring (before the missing-ids check):

```python
    if any(layer.placeholder is not None for layer in construction.layers):
        raise ValueError("cannot export placeholder layers; assign materialsdb materials first")
```

Also check `apply_add_construction`'s sibling in `construction.py`'s `_purge_prior_layer_sets` / `to_ifc_layer_set` body for `store_.get(layer.material_id)` calls — with placeholders rejected up front, the remaining loop only sees resolvable layers (no change needed).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_construction.py tests/test_construction_ifc.py -q`
Expected: PASS (all pre-existing tests included).

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/construction.py tests/test_construction.py
git commit -m "feat(construction): placeholder layers for model materials without identity"
```

---

### Task 2: Server — incoming queue

**Files:**
- Modify: `src/materialsdb/gui/server.py`
- Test: `tests/test_gui_server.py`

**Interfaces:**
- Consumes: `construction.finite_or_none` (Task 1); `GuiState`.
- Produces: `GuiState.incoming` (list, newest first, cap 20); endpoints `POST /api/composer/push`, `GET /api/composer/incoming`, `POST /api/composer/incoming/consume` `{index}` → `{construction: item}` (pop). Incoming item shape: `{"name": str, "design_usage": str | None, "layers": [{"material_id": str | None, "thickness_m": float, "placeholder": {"name", "lambda_value"} | None}]}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_server.py`:

```python
def test_composer_push_list_consume(api):
    server, state = api
    body = {
        "name": "Mur 20+16",
        "design_usage": "consDesignForWall",
        "layers": [
            {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2},
            {"material_id": None, "thickness_m": 0.18, "placeholder": {"name": "Brique", "lambda_value": 0.21}},
        ],
    }
    status, _ = request(server, "POST", "/api/composer/push", payload=body, token=state.token)
    assert status == 200

    status, listed = request(server, "GET", "/api/composer/incoming", token=state.token)
    assert status == 200
    assert listed["incoming"][0]["name"] == "Mur 20+16"
    assert listed["incoming"][0]["layers"][1]["placeholder"] == {"name": "Brique", "lambda_value": 0.21}

    status, consumed = request(server, "POST", "/api/composer/incoming/consume", payload={"index": 0}, token=state.token)
    assert status == 200
    assert consumed["construction"]["name"] == "Mur 20+16"
    assert request(server, "GET", "/api/composer/incoming", token=state.token)[1]["incoming"] == []


def test_composer_push_validation(api):
    server, state = api
    status, body = request(server, "POST", "/api/composer/push", payload={"name": "", "layers": []}, token=state.token)
    assert status == 400
    assert "name required" in body["error"]
    status, body = request(server, "POST", "/api/composer/push", payload={"name": "x", "layers": []}, token=state.token)
    assert status == 400
    assert "at least one layer required" in body["error"]
    status, body = request(
        server,
        "POST",
        "/api/composer/push",
        payload={"name": "x", "layers": [{"material_id": None, "thickness_m": -1}]},
        token=state.token,
    )
    assert status == 400
    assert "thickness must be >= 0" in body["error"]


def test_composer_consume_bad_index(api):
    server, state = api
    status, body = request(server, "POST", "/api/composer/incoming/consume", payload={"index": 3}, token=state.token)
    assert status == 404
    assert "no incoming at index 3" in body["error"]


def test_composer_incoming_capped_at_20(api):
    server, state = api
    for index in range(22):
        status, _ = request(
            server,
            "POST",
            "/api/composer/push",
            payload={"name": f"c{index}", "layers": [{"material_id": None, "thickness_m": 0.1, "placeholder": {"name": "", "lambda_value": None}}]},
            token=state.token,
        )
        assert status == 200
    status, listed = request(server, "GET", "/api/composer/incoming", token=state.token)
    assert len(listed["incoming"]) == 20
    assert listed["incoming"][0]["name"] == "c21"  # newest first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -q`
Expected: FAIL — the new routes 404 / fall through.

- [ ] **Step 3: Implement**

In `src/materialsdb/gui/server.py`:

Add `math` to the stdlib import group.

`GuiState.__init__` — append:

```python
        # constructions pushed from a model (newest first, capped at 20)
        self.incoming = []
```

`do_GET` — add after the listener clients route:

```python
        if parsed.path == "/api/composer/incoming":
            self._send(200, {"incoming": self.state.incoming})
            return
```

`do_POST` — add before the `/api/constructions/` save route:

```python
            elif parsed.path == "/api/composer/push":
                self._composer_push(payload)
            elif parsed.path == "/api/composer/incoming/consume":
                self._composer_consume(payload)
```

New methods on `GuiHandler` (next to the listener helpers):

```python
    def _composer_push(self, payload):
        name = str(payload.get("name") or "").strip()
        layers = payload.get("layers")
        if not name:
            self._send(400, {"error": "name required"})
            return
        if not isinstance(layers, list) or not layers:
            self._send(400, {"error": "at least one layer required"})
            return
        checked = []
        for index, entry in enumerate(layers):
            if not isinstance(entry, dict):
                self._send(400, {"error": f"layer {index}: invalid entry"})
                return
            from materialsdb.construction import finite_or_none

            thickness = finite_or_none(entry.get("thickness_m"))
            if thickness is None or thickness < 0:
                self._send(400, {"error": f"layer {index}: thickness must be >= 0"})
                return
            layer = {"material_id": entry.get("material_id") or None, "thickness_m": thickness}
            placeholder = entry.get("placeholder")
            if placeholder is not None:
                layer["placeholder"] = {
                    "name": str(placeholder.get("name") or ""),
                    "lambda_value": finite_or_none(placeholder.get("lambda_value")),
                }
            checked.append(layer)
        self.state.incoming.insert(0, {
            "name": name,
            "design_usage": payload.get("design_usage") or None,
            "layers": checked,
        })
        del self.state.incoming[20:]
        self._send(200, {"ok": True, "count": len(self.state.incoming)})

    def _composer_consume(self, payload):
        try:
            index = int(payload.get("index"))
        except (TypeError, ValueError):
            self._send(400, {"error": "index required"})
            return
        if index < 0 or index >= len(self.state.incoming):
            self._send(404, {"error": f"no incoming at index {index}"})
            return
        item = self.state.incoming.pop(index)
        self._send(200, {"construction": item})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/server.py tests/test_gui_server.py
git commit -m "feat(gui): incoming construction queue for model pushes"
```

---

### Task 3: Server — push-back placeholder passthrough

**Files:**
- Modify: `src/materialsdb/gui/listener.py` (`build_add_construction_payload`)
- Test: `tests/test_listener_payload.py`

**Interfaces:**
- Consumes: Task 1's placeholder-aware `validate_construction`.
- Produces: payload construction layers gain `{"material_id": None, "thickness_m": float, "placeholder": {name, lambda_value}}` entries (no `material` dict) for placeholder layers; resolvable layers unchanged. `queued` counts ALL layers.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_listener_payload.py`:

```python
def test_build_add_construction_placeholder_passthrough(store):
    from materialsdb.gui.listener import build_add_construction_payload

    payload, problems = build_add_construction_payload(
        store_,
        body={
            "name": "Mixed wall",
            "design_usage": "consDesignForWall",
            "layers": [
                {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2},
                {"material_id": None, "thickness_m": 0.18, "placeholder": {"name": "Brique", "lambda_value": 0.21}},
            ],
        },
    )

    assert problems == []
    layers = payload["construction"]["layers"]
    assert layers[0]["material_id"] == "00000000-0000-0000-0000-000000000001"
    assert "placeholder" not in layers[0]
    assert layers[1] == {
        "material_id": None,
        "thickness_m": 0.18,
        "placeholder": {"name": "Brique", "lambda_value": 0.21},
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py -q`
Expected: FAIL — placeholder layers hit the `store_.get(None)` path and land in `problems` / raise.

- [ ] **Step 3: Implement**

In `src/materialsdb/gui/listener.py`, `build_add_construction_payload`'s layer loop — add a placeholder branch BEFORE the summary/store lookups:

```python
    for layer in construction.layers:
        if layer.placeholder is not None:
            layers.append(
                {
                    "material_id": None,
                    "thickness_m": layer.thickness_m,
                    "placeholder": dict(layer.placeholder),
                }
            )
            continue
        summary = store_.get_summary(layer.material_id)
        material = store_.get(layer.material_id)
        # ... existing resolvable-layer code unchanged ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py tests/test_gui_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/listener.py tests/test_listener_payload.py
git commit -m "feat(gui): placeholder layers pass through construction push-back"
```

---

### Task 4: `insert.py` — util-based reads (refactor)

**Files:**
- Modify: `bonsai_addon/insert.py`
- Test: `tests/test_bonsai_insert.py` (existing suite is the characterization net; plus one new read test)

**Interfaces:**
- Consumes: `ifcopenshell.util.element.get_psets(material) -> {"materialsdb": {"material_id": …}, "materialsdb.org_layer": {"layer_id": …}, …}` (works for `IfcMaterial`, includes custom psets, adds an `"id"` key).
- Produces: helpers replaced by `_material_id_of(material) -> str | None` and `_org_layer_id_of(material) -> str | None`; `existing_materials_by_id(file)` and `_existing_keys(file)` keep signatures/semantics (first material wins per id; key = `(material_id, layer_id | None)`). `_pset_props`/`_materials_of` are DELETED (verify no other references first).

- [ ] **Step 1: Add a read test (characterization for the refactor)**

Append to `tests/test_bonsai_insert.py`:

```python
def test_existing_materials_by_id_reads_via_psets(store):
    file = ifcopenshell.file(schema="IFC4")
    apply_add_materials(file, _payload(store))

    found = existing_materials_by_id(file)

    assert set(found) == {"00000000-0000-0000-0000-000000000001"}
    assert found["00000000-0000-0000-0000-000000000001"].Name == "Isolant A"
```

Add `existing_materials_by_id` to the import line at the top of the test file (same `# ty: ignore[unresolved-import]` comment style).

- [ ] **Step 2: Run tests to verify they pass BEFORE the refactor**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: PASS (current raw-scan code satisfies this). Safety net.

- [ ] **Step 3: Refactor the reads**

In `bonsai_addon/insert.py`:

Add to the imports:

```python
import ifcopenshell.api
import ifcopenshell.util.element
```

Replace `_pset_props`, `_materials_of`, `existing_materials_by_id`, `_existing_keys` with:

```python
def _material_id_of(material) -> str | None:
    return ifcopenshell.util.element.get_psets(material).get("materialsdb", {}).get("material_id")


def _org_layer_id_of(material) -> str | None:
    return ifcopenshell.util.element.get_psets(material).get("materialsdb.org_layer", {}).get("layer_id")


def existing_materials_by_id(file):
    """material_id -> first IfcMaterial carrying a matching materialsdb pset."""
    found = {}
    for material in file.by_type("IfcMaterial"):
        material_id = _material_id_of(material)
        if material_id is not None and material_id not in found:
            found[material_id] = material
    return found


def _existing_keys(file):
    """(material_id, layer_id | None) pairs already present, from the
    materialsdb identity + org_layer psets."""
    keys = set()
    for material in file.by_type("IfcMaterial"):
        material_id = _material_id_of(material)
        if material_id is not None:
            keys.add((material_id, _org_layer_id_of(material)))
    return keys
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py tests/test_bonsai_discovery.py -q`
Expected: PASS (entire suite is the characterization net — idempotence/subset/thick-less tests must stay green unchanged).

- [ ] **Step 5: Commit**

```bash
git add bonsai_addon/insert.py tests/test_bonsai_insert.py
git commit -m "refactor(bonsai): read materialsdb psets via ifcopenshell.util.element.get_psets"
```

---

### Task 5: `insert.py` — push-back placeholder branch

**Files:**
- Modify: `bonsai_addon/insert.py` (`apply_add_construction`)
- Modify: `bonsai_addon/__init__.py` (`_execute` detail string)
- Test: `tests/test_bonsai_insert.py`

**Interfaces:**
- Consumes: Task 3 payload shape (placeholder entries), Task 4 reads.
- Produces: `apply_add_construction` summary gains `"placeholders_matched": int` (total keys now `{"types_created", "sets_updated", "materials_created", "placeholders_matched"}`); placeholder layers re-attach the model material by `Name == placeholder.name` or create it with a `Pset_MaterialThermal` pset (λ persists in the model).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bonsai_insert.py`:

```python
MIXED_PAYLOAD = {
    "action": "add_construction",
    "construction": {
        "name": "Mixed wall",
        "design_usage": "consDesignForWall",
        "types": ["IfcWallType"],
        "layers": [
            {
                "material_id": "00000000-0000-0000-0000-000000000001",
                "thickness_m": 0.2,
                "material": {
                    "source_id": "00000000-0000-0000-0000-000000000001",
                    "name": "Isolant A",
                    "description": "Panneau isolant",
                    "category": "Insulation",
                    "color": 16711680,
                    "identity": {
                        "material_id": "00000000-0000-0000-0000-000000000001",
                        "company_id": "A1B85A67-5B1E-4960-A297-2DE8275049C5",
                        "company": "Mini SA",
                    },
                },
            },
            {
                "material_id": None,
                "thickness_m": 0.18,
                "placeholder": {"name": "Brique terrecuite", "lambda_value": 0.21},
            },
        ],
    },
}


def test_construction_placeholder_matches_model_material_by_name():
    file = ifcopenshell.file(schema="IFC4")
    foreign = ifcopenshell.api.run("material.add_material", file, name="Brique terrecuite")
    thermal = ifcopenshell.api.run("pset.add_pset", file, product=foreign, name="Pset_MaterialThermal")
    ifcopenshell.api.run("pset.edit_pset", file, pset=thermal, properties={"ThermalConductivity": 0.21})
    materials_before = len(file.by_type("IfcMaterial"))

    summary = apply_add_construction(file, MIXED_PAYLOAD)

    assert summary["placeholders_matched"] == 1
    assert summary["materials_created"] == 1  # only Isolant A; Brique reused
    assert len(file.by_type("IfcMaterial")) == materials_before  # no duplicate
    layer_set = file.by_type("IfcWallType")[0].HasAssociations[0].RelatingMaterial
    layers = sorted(layer_set.MaterialLayers, key=lambda l: l.LayerThickness)
    assert layers[0].Material.Name == "Brique terrecuite"
    assert layers[1].Material.Name == "Isolant A"


def test_construction_placeholder_created_with_thermal_when_absent():
    file = ifcopenshell.file(schema="IFC4")

    summary = apply_add_construction(file, MIXED_PAYLOAD)

    assert summary["placeholders_matched"] == 0
    assert summary["materials_created"] == 2
    brique = [m for m in file.by_type("IfcMaterial") if m.Name == "Brique terrecuite"][0]
    thermal = ifcopenshell.util.element.get_psets(brique).get("Pset_MaterialThermal", {})
    assert thermal.get("ThermalConductivity") == 0.21
```

(extend the top import to `from insert import apply_add_construction, apply_add_materials` with the existing comment style; add `import ifcopenshell.util.element` to the test imports if not present.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: FAIL — KeyError/ValueError on placeholder layers (current code assumes `layer["material"]`).

- [ ] **Step 3: Implement**

In `bonsai_addon/insert.py`, `apply_add_construction`:

Change the summary init to:

```python
    summary = {"types_created": 0, "sets_updated": 0, "materials_created": 0, "placeholders_matched": 0}
```

Add a module-level helper (after `_apply_style`):

```python
def _find_material_by_name(file, name):
    if not name:
        return None
    return next((m for m in file.by_type("IfcMaterial") if m.Name == name), None)


def _create_placeholder_material(file, placeholder):
    material = ifcopenshell.api.run(
        "material.add_material", file, name=str(placeholder.get("name") or "(unnamed)")
    )
    lambda_value = placeholder.get("lambda_value")
    if lambda_value:
        thermal = ifcopenshell.api.run("pset.add_pset", file, product=material, name="Pset_MaterialThermal")
        ifcopenshell.api.run(
            "pset.edit_pset", file, pset=thermal, properties={"ThermalConductivity": float(lambda_value)}
        )
    return material
```

In the layer loop, add the placeholder branch before the resolvable branch:

```python
    layers = []
    for layer in construction["layers"]:
        if layer.get("placeholder") is not None:
            name = str(layer["placeholder"].get("name") or "")
            material = _find_material_by_name(file, name)
            if material is None:
                material = _create_placeholder_material(file, layer["placeholder"])
            else:
                summary["placeholders_matched"] += 1
            layers.append((layer, material))
            continue
        material = known.get(layer["material_id"])
        # ... existing resolvable code unchanged ...
```

Also in `bonsai_addon/__init__.py`, extend the operator's applied detail string:

```python
            elif action == "add_construction":
                result = insert.apply_add_construction(tool.Ifc.get(), payload)
                _link_pushed_types(tool.Ifc.get(), payload["construction"])
                _CLIENT.report(
                    "applied",
                    f"{result['types_created']} type(s) created, {result['sets_updated']} set(s) updated, "
                    f"{result['materials_created']} material(s) created, "
                    f"{result['placeholders_matched']} placeholder(s) matched",
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Two existing tests assert the summary as an EXACT dict and will break with the new key — update their literals to include `"placeholders_matched": 0`:
- `test_construction_creates_type_and_layers`: `assert summary == {"types_created": 1, "sets_updated": 0, "materials_created": 2, "placeholders_matched": 0}`
- `test_construction_resend_updates_same_set`: `assert summary == {"types_created": 0, "sets_updated": 1, "materials_created": 0, "placeholders_matched": 0}`

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: PASS (all construction tests with the updated literals).

- [ ] **Step 5: Commit**

```bash
git add bonsai_addon/insert.py tests/test_bonsai_insert.py
git commit -m "feat(bonsai): re-attach model materials by name on construction push-back"
```

---

### Task 6: `read_construction.py` — model → construction reader

**Files:**
- Create: `bonsai_addon/read_construction.py`
- Test: `tests/test_bonsai_insert.py` (add to the same side-path import style)

**Interfaces:**
- Consumes: `ifcopenshell.util.element.get_type/get_material/get_psets`.
- Produces: `ReadError(ValueError)`; `read_construction_from_element(element) -> {"name": str, "design_usage": str | None, "layers": [{"material_id": str | None, "thickness_m": float, "placeholder": {"name", "lambda_value"} | None}]}` — matching the incoming-queue item shape (Task 2).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bonsai_insert.py`:

```python
def _make_type_with_layer_set(file, type_class="IfcWallType", name="Mur 20+16"):
    type_element = ifcopenshell.api.run("root.create_entity", file, ifc_class=type_class, name=name)
    layer_set = ifcopenshell.api.run("material.add_material_set", file, name=name, set_type="IfcMaterialLayerSet")
    ifcopenshell.api.run("material.assign_material", file, products=[type_element], type="IfcMaterialLayerSet", material=layer_set)
    return type_element, layer_set


def test_read_construction_from_occurrence():
    file = ifcopenshell.file(schema="IFC4")
    type_element, layer_set = _make_type_with_layer_set(file)
    ifcopenshell.api.run("type.assign_type", file, related_objects=[occurrence := ifcopenshell.api.run("root.create_entity", file, ifc_class="IfcWall", name="W")], relating_type=type_element)
    resolvable = ifcopenshell.api.run("material.add_material", file, name="Isolant A")
    identity = ifcopenshell.api.run("pset.add_pset", file, product=resolvable, name="materialsdb")
    ifcopenshell.api.run("pset.edit_pset", file, pset=identity, properties={"material_id": "00000000-0000-0000-0000-000000000001"})
    foreign = ifcopenshell.api.run("material.add_material", file, name="Brique terrecuite")
    thermal = ifcopenshell.api.run("pset.add_pset", file, product=foreign, name="Pset_MaterialThermal")
    ifcopenshell.api.run("pset.edit_pset", file, pset=thermal, properties={"ThermalConductivity": 0.21})
    ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=resolvable)
    ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=foreign)
    layers = layer_set.MaterialLayers
    ifcopenshell.api.run("material.edit_layer", file, layer=layers[0], attributes={"LayerThickness": 0.2})
    ifcopenshell.api.run("material.edit_layer", file, layer=layers[1], attributes={"LayerThickness": 0.18})

    construction = read_construction_from_element(occurrence)

    assert construction["name"] == "Mur 20+16"
    assert construction["design_usage"] == "consDesignForWall"
    assert construction["layers"][0] == {
        "material_id": "00000000-0000-0000-0000-000000000001",
        "thickness_m": 0.2,
        "placeholder": None,
    }
    assert construction["layers"][1] == {
        "material_id": None,
        "thickness_m": 0.18,
        "placeholder": {"name": "Brique terrecuite", "lambda_value": 0.21},
    }


def test_read_construction_from_type_directly():
    file = ifcopenshell.file(schema="IFC4")
    type_element, _ = _make_type_with_layer_set(file, type_class="IfcSlabType", name="Dalle")

    construction = read_construction_from_element(type_element)

    assert construction["design_usage"] == "consDesignForFloor"


def test_read_construction_errors():
    import pytest as _pytest

    file = ifcopenshell.file(schema="IFC4")
    occurrence = ifcopenshell.api.run("root.create_entity", file, ifc_class="IfcWall", name="W")

    with _pytest.raises(ReadError, match="selected element has no type"):
        read_construction_from_element(occurrence)

    type_element, _ = _make_type_with_layer_set(file)  # no material assigned
    with _pytest.raises(ReadError, match="type has no material"):
        read_construction_from_element(type_element)

    type_element2, _ = _make_type_with_layer_set(file, name="T2")
    plain = ifcopenshell.api.run("material.add_material", file, name="M")
    ifcopenshell.api.run("material.assign_material", file, products=[type_element2], type="IfcMaterial", material=plain)
    with _pytest.raises(ReadError, match="material is not an IfcMaterialLayerSet: IfcMaterial"):
        read_construction_from_element(type_element2)


def test_read_construction_materialless_layer_becomes_placeholder():
    file = ifcopenshell.file(schema="IFC4")
    type_element, layer_set = _make_type_with_layer_set(file)
    ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=None)

    construction = read_construction_from_element(type_element)

    assert construction["layers"][0]["material_id"] is None
    assert construction["layers"][0]["thickness_m"] == 0.0  # LayerThickness never set
    assert construction["layers"][0]["placeholder"] == {"name": "", "lambda_value": None}
```

(add `read_construction_from_element, ReadError` to the side-path import line — note this file lives at `bonsai_addon/read_construction.py`, same `sys.path` insertion already used for `insert`/`discovery`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'read_construction'`.

- [ ] **Step 3: Implement**

Create `bonsai_addon/read_construction.py`:

```python
"""Read a construction (type + IfcMaterialLayerSet) from a Bonsai model.

Pure ifcopenshell + utils (no bpy/bonsai imports) so CI can test it."""

import ifcopenshell.util.element


class ReadError(ValueError):
    """The selected element cannot yield a layer-set construction."""


_DESIGN_USAGE_BY_CLASS = {
    "IfcWallType": "consDesignForWall",
    "IfcSlabType": "consDesignForFloor",
    "IfcRoofType": "consDesignForRoof",
    "IfcWall": "consDesignForWall",
    "IfcSlab": "consDesignForFloor",
    "IfcRoof": "consDesignForRoof",
}


def read_construction_from_element(element) -> dict:
    if element.is_a("IfcTypeProduct"):
        type_element = element
    else:
        type_element = ifcopenshell.util.element.get_type(element)
        if type_element is None:
            raise ReadError("selected element has no type")
    material = ifcopenshell.util.element.get_material(type_element)
    if material is None:
        raise ReadError("type has no material")
    if not material.is_a("IfcMaterialLayerSet"):
        raise ReadError(f"material is not an IfcMaterialLayerSet: {material.is_a()}")
    layers = []
    for layer in material.MaterialLayers or ():
        thickness = layer.LayerThickness
        entry = {
            "material_id": None,
            "thickness_m": float(thickness) if thickness else 0.0,
            "placeholder": None,
        }
        if layer.Material is not None:
            psets = ifcopenshell.util.element.get_psets(layer.Material)
            material_id = psets.get("materialsdb", {}).get("material_id")
            if material_id:
                entry["material_id"] = str(material_id)
            else:
                entry["placeholder"] = {
                    "name": str(layer.Material.Name or ""),
                    "lambda_value": psets.get("Pset_MaterialThermal", {}).get("ThermalConductivity"),
                }
        else:
            entry["placeholder"] = {"name": "", "lambda_value": None}
        layers.append(entry)
    return {
        "name": str(type_element.Name or ""),
        "design_usage": _DESIGN_USAGE_BY_CLASS.get(type_element.is_a()),
        "layers": layers,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bonsai_addon/read_construction.py tests/test_bonsai_insert.py
git commit -m "feat(bonsai): read a type layer set as a construction (utils-based)"
```

---

### Task 7: Add-on — send-to-composer operator + panel button

**Files:**
- Modify: `bonsai_addon/discovery.py` (`ListenerClient.send_to_composer`)
- Modify: `bonsai_addon/__init__.py`
- Test: none (bpy code; verified by Task 9's harness + gates)

**Interfaces:**
- Consumes: Task 6 reader; `ListenerClient._request` (existing).
- Produces: `ListenerClient.send_to_composer(construction) -> None` (POSTs `/api/composer/push`); `bpy.ops.materialsdb.send_construction()`.

- [ ] **Step 1: `discovery.py` — public composer push**

Add to `ListenerClient` (after `report`):

```python
    def send_to_composer(self, construction):
        self._request(
            "POST",
            "/api/composer/push",
            {
                "name": construction["name"],
                "design_usage": construction.get("design_usage"),
                "layers": construction["layers"],
            },
        )
```

- [ ] **Step 2: `__init__.py` — operator + panel**

Imports: add `from .read_construction import ReadError, read_construction_from_element`.

New operator (place before `MATERIALSDB_PT_panel`):

```python
class MATERIALSDB_OT_send_construction(bpy.types.Operator):
    bl_idname = "materialsdb.send_construction"
    bl_label = "materialsdb send construction"
    bl_description = "Push the active object's type layer set to the composer (read-only)"
    bl_options: typing.ClassVar[set[str]] = {"REGISTER"}

    def execute(self, context):
        obj = context.active_object
        element = tool.Ifc.get_entity(obj) if obj is not None else None
        if element is None:
            self.report({"ERROR"}, "select an IFC element")
            return {"CANCELLED"}
        try:
            construction = read_construction_from_element(element)
        except ReadError as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        try:
            ListenerClient().send_to_composer(construction)
        except Exception as err:  # noqa: BLE001 - surface, never crash Blender
            self.report({"ERROR"}, f"materialsdb-gui not reachable: {err}")
            return {"CANCELLED"}
        placeholders = sum(1 for layer in construction["layers"] if layer["placeholder"])
        self.report(
            {"INFO"},
            f"sent {len(construction['layers'])} layer(s) ({placeholders} model material) to the composer",
        )
        return {"FINISHED"}
```

Panel `draw` gains (after the toggle operator row):

```python
        layout.operator("materialsdb.send_construction", text="Send type to composer")
```

`register()`/`unregister()` gain the new class (register before the panel, unregister in reverse order).

NOTE: a fresh `ListenerClient()` per push is intentional — the token comes from the discovery file; no registration needed for one-shot pushes.

- [ ] **Step 3: Gates**

Run: `ruff check --exclude src/materialsdb/classes.py bonsai_addon && ruff format --exclude src/materialsdb/classes.py --check . && python3 -m pytest -p no:pytest-blender -q && ty check --project .`
Expected: all clean; main suite unchanged.

- [ ] **Step 4: Commit**

```bash
git add bonsai_addon/discovery.py bonsai_addon/__init__.py
git commit -m "feat(bonsai): push the active type's layer set to the composer"
```

---

### Task 8: Composer GUI — incoming section + placeholder rows

**Files:**
- Modify: `src/materialsdb/gui/static/constructions.html`
- Modify: `src/materialsdb/gui/static/app-constructions.js`
- Test: `tests/harness/construction_page_harness.mjs` (extend if cheap; otherwise rely on node --check + manual checklist, consistent with prior rounds)

**Interfaces:**
- Consumes: Task 2 endpoints; Task 1 placeholder model.
- Produces: `#incoming-list` section; placeholder-aware `renderLayers`/save/send/`layerChoicesInitAll`.

- [ ] **Step 1: HTML — incoming section**

In `constructions.html`, inside `#saved` (before the `<hr>`), add:

```html
  <hr>
  <div class="label">incoming from model</div>
  <ul id="incoming-list" style="list-style:none;padding:0;font-size:.85rem"></ul>
```

- [ ] **Step 2: JS — incoming poll + consume-and-load**

In `app-constructions.js`, near `loadList`:

```javascript
let incoming = [];

async function refreshIncoming() {
  try {
    incoming = (await api("/api/composer/incoming")).incoming;
  } catch {
    incoming = [];
  }
  const box = $("incoming-list");
  box.innerHTML = incoming.length
    ? incoming.map((construction, index) =>
        `<li><a href="#" data-index="${index}">${esc(construction.name)}</a> ` +
        `<span style="color:#888">· ${construction.layers.length} layer(s)` +
        `${construction.layers.some((l) => l.placeholder) ? " · has model materials" : ""}</span></li>`).join("")
    : `<li style="color:#777;font-size:.8rem">nothing incoming from the model</li>`;
  box.querySelectorAll("a").forEach((a) => a.addEventListener("click", async (event) => {
    event.preventDefault();
    const result = await api("/api/composer/incoming/consume", {
      method: "POST",
      body: JSON.stringify({ index: Number(a.dataset.index) }),
    });
    loadEditable(result.construction);
    setStatus(`editing copy of ${result.construction.name} — save to keep, send to: to push back`);
    refreshIncoming();
  }));
}
setInterval(refreshIncoming, 2000);
refreshIncoming();
```

- [ ] **Step 3: JS — placeholder-aware layers**

In `layerChoicesInitAll`, skip placeholders:

```javascript
async function layerChoicesInitAll() {
  await Promise.all(layers.map(async (layer) => {
    if (layer.material_id) Object.assign(layer, await fetchLayerChoices(layer.material_id));
  }));
  await refreshU();
}
```

In `renderLayers`, the name cell becomes placeholder-aware (badge + no dropdown effect lives in thicknessCellHtml? — placeholders keep their editable thickness cell as-is):

```javascript
    const isPlaceholder = !layer.material_id && layer.placeholder;
    const nameCell = isPlaceholder
      ? `${esc(layer.placeholder.name || "(model material)")} <span style="color:#888;font-size:.8rem">model material</span>`
      : esc(layer.display_name || layer.material_id);
    return `<tr data-index="${index}"${selectedAttr} style="cursor:pointer;border-left:4px solid ${catColor}">` +
      `<td>${index + 1}</td><td data-role="name">${nameCell}</td>` +
      `<td>${thicknessCellHtml(layer, index)}</td>` +
      `<td data-role="lambda">${esc(layer.lambda_value ?? (isPlaceholder ? layer.placeholder.lambda_value ?? "" : ""))}</td><td data-role="r">${esc(fmtR(layer))}</td><td></td></tr>`;
```

(Adapt minimally to the current renderLayers — the contract is: placeholder rows show `placeholder.name` (or "(model material)"), their λ from `placeholder.lambda_value`, and NO material dropdown appears for them. Check `thicknessCellHtml`/dropdown wiring for material-dependent options and skip the dropdown for placeholder rows.)

Save + send handlers map placeholders through:

```javascript
      layers: layers.map((l) => ({
        material_id: l.material_id,
        thickness_m: l.thickness_m,
        ...(l.placeholder ? { placeholder: { name: l.placeholder.name, lambda_value: l.placeholder.lambda_value } } : {}),
      })),
```

(apply to BOTH the `$("save").onclick` body and the `$("send-bonsai").onclick` construction body — keep the rest of each handler unchanged.)

- [ ] **Step 4: Verify**

Run: `node --check src/materialsdb/gui/static/app-constructions.js && node tests/harness/construction_page_harness.mjs src/materialsdb/gui/static/app-constructions.js && python3 -m pytest -p no:pytest-blender tests/test_construction_frontend.py -q`
Expected: pass (extend the harness's DOM stubs only if the new code needs them — e.g. `api` already stubbed).

Run: `python3 -m pytest -p no:pytest-blender -q && ruff format --exclude src/materialsdb/classes.py --check .`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/static/constructions.html src/materialsdb/gui/static/app-constructions.js tests/harness/construction_page_harness.mjs
git commit -m "feat(gui): incoming-from-model section and placeholder layer rows"
```

---

### Task 9: In-Blender harness — full round-trip

**Files:**
- Modify: `bonsai_addon/test/test_in_blender.py`

**Interfaces:**
- Consumes: Tasks 1-8.

- [ ] **Step 1: Write the test**

Append to `bonsai_addon/test/test_in_blender.py`:

```python
def test_construction_roundtrip_from_model(tmp_path):
    """Select an occurrence, push its layer set to the composer, consume it,
    edit, push back: same set entity updated, placeholder material re-attached
    by name (no duplicate IfcMaterial)."""
    import bpy

    server, _cache_dir, info, old_cache_env = _seeded_server(tmp_path)
    try:
        port, token = info["port"], info["token"]
        file = tool.Ifc.get()

        # build a model-side type + occurrence with a mixed layer set
        type_element = ifcopenshell.api.run("root.create_entity", file, ifc_class="IfcWallType", name="Mur 20+16")
        occurrence = ifcopenshell.api.run("root.create_entity", file, ifc_class="IfcWall", name="Mur occurrence")
        ifcopenshell.api.run("type.assign_type", file, related_objects=[occurrence], relating_type=type_element)
        layer_set = ifcopenshell.api.run("material.add_material_set", file, name="Mur 20+16", set_type="IfcMaterialLayerSet")
        ifcopenshell.api.run("material.assign_material", file, products=[type_element], type="IfcMaterialLayerSet", material=layer_set)
        resolvable = ifcopenshell.api.run("material.add_material", file, name="Isolant A")
        identity = ifcopenshell.api.run("pset.add_pset", file, product=resolvable, name="materialsdb")
        ifcopenshell.api.run("pset.edit_pset", file, pset=identity, properties={"material_id": "00000000-0000-0000-0000-000000000001"})
        foreign = ifcopenshell.api.run("material.add_material", file, name="Brique terrecuite")
        thermal = ifcopenshell.api.run("pset.add_pset", file, product=foreign, name="Pset_MaterialThermal")
        ifcopenshell.api.run("pset.edit_pset", file, pset=thermal, properties={"ThermalConductivity": 0.21})
        layer_a = ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=resolvable)
        layer_b = ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=foreign)
        ifcopenshell.api.run("material.edit_layer", file, layer=layer_a, attributes={"LayerThickness": 0.2})
        ifcopenshell.api.run("material.edit_layer", file, layer=layer_b, attributes={"LayerThickness": 0.18})

        # an active occurrence object so the panel operator has a selection
        obj = bpy.data.objects.new("Mur occurrence", None)
        tool.Ifc.link(occurrence, obj)
        tool.Root.set_object_name(obj, occurrence)
        tool.Collector.assign(obj)
        bpy.context.view_layer.objects.active = obj

        previous = bonsai_addon._CLIENT
        bonsai_addon._CLIENT = client = ListenerClient()
        try:
            assert bpy.ops.materialsdb.send_construction() == {"FINISHED"}
        finally:
            bonsai_addon._CLIENT = previous

        status, listed = _request(port, token, "GET", "/api/composer/incoming", token=token)
        assert status == 200
        assert listed["incoming"][0]["name"] == "Mur 20+16"
        assert listed["incoming"][0]["layers"][1]["placeholder"] == {"name": "Brique terrecuite", "lambda_value": 0.21}

        # composer side: consume + edit (thickness change) + push back
        status, consumed = _request(port, token, "POST", "/api/composer/incoming/consume", payload={"index": 0}, token=token)
        assert status == 200
        construction = consumed["construction"]
        construction["layers"][0]["thickness_m"] = 0.25
        status, body = _request(
            port,
            token,
            "POST",
            "/api/listener/send",
            {"client_id": client.client_id, "action": "add_construction", "construction": construction},
        )
        assert status == 200, body

        materials_before = len(file.by_type("IfcMaterial"))
        try:
            assert bonsai_addon._poll_timer() == 1.0
        finally:
            bonsai_addon._CLIENT = previous

        layer_set_after = file.by_type("IfcMaterialLayerSet")[0]
        assert {round(l.LayerThickness, 3) for l in layer_set_after.MaterialLayers} == {0.25, 0.18}
        assert len(file.by_type("IfcMaterial")) == materials_before  # placeholder re-attached, not duplicated
        assert len(file.by_type("IfcWallType")) == 1
        assert bpy.data.objects.get("IfcWallType/Mur 20+16") is not None  # still outliner-linked
    finally:
        if server is not None:
            _stop_server(server, old_cache_env)
```

Check the test file's imports: `ifcopenshell.api` is used — add `import ifcopenshell.api` at the top if absent (the file currently imports `ifcopenshell` for `ifcopenshell.file`).

- [ ] **Step 2: Run the harness**

Run: `pytest bonsai_addon/test -q`
Expected: 5 passed. If the push-back creates a duplicate "Brique terrecuite", investigate the name-match (trailing spaces? `get_psets` normalization?) — do NOT weaken the assertion.

- [ ] **Step 3: Commit**

```bash
git add bonsai_addon/test/test_in_blender.py
git commit -m "test(bonsai): full construction round-trip through the composer in real Blender"
```

---

### Task 10: Docs + full gates + zip

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README**

In the "Bonsai integration :" section, after the pushed-constructions sentence, add:

```markdown
Round-trip: select the wall (or its type) in Bonsai and hit *Send type to
composer* in the materialsdb panel — the construction appears under
*incoming from model* on the constructions page for editing. Layers whose
material is not a materialsdb material are kept as "model material"
placeholders (λ read from the model) and re-attach to the same material on
push-back.
```

- [ ] **Step 2: Full gates**

Run: `python3 -m pytest -p no:pytest-blender -q && ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon && ruff format --exclude src/materialsdb/classes.py --check . && ty check --project . && pytest bonsai_addon/test -q && python3 dev_utils/build_bonsai_addon.py`
Expected: all green; zip rebuilt.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: construction round-trip with the composer"
```
