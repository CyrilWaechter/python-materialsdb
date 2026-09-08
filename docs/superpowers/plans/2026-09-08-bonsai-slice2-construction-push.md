# Bonsai Slice 2 — Construction Push + Undoable API Insertion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push constructions from the maker into the active Bonsai model as upserted typed elements (`IfcWallType`/`IfcSlabType`/`IfcRoofType` per `design_usage`, one shared `IfcMaterialLayerSet`), rework slice-1 insertion on `ifcopenshell.api`, and make every push revertible with Blender Ctrl+Z.

**Architecture:** Server resolves construction bodies into self-sufficient payloads (find-or-create materials by identity pset). The add-on applies pushes inside a real operator inheriting bonsai's `tool.Ifc.Operator` (transaction + undo via `IfcStore.execute_ifc_operator`); `insert.py` stays pure ifcopenshell/api for CI. The in-Blender harness verifies insertion AND undo/redo against real Blender+Bonsai.

**Tech Stack:** stdlib `http.server` (server), `ifcopenshell.api` 0.9 kwargs (insert), `bpy`/bonsai `tool.Ifc.Operator` (add-on), vanilla JS (`picker-core.js` shared target poll).

## Global Constraints

- Python `>=3.10`; **no new runtime dependencies**; server stays stdlib-only; `insert.py` imports ONLY `ifcopenshell`(+api) — never bpy/bonsai.
- ruff line-length 120; local main suite `python3 -m pytest -p no:pytest-blender -q`; in-Blender harness runs WITHOUT that flag: `pytest bonsai_addon/test -q`.
- api kwargs are **0.9**: `root.create_entity(ifc_class=…)` (NOT `class=`); `material.add_layer` takes **no thickness** — set it with `material.edit_layer(layer=…, attributes={"LayerThickness": …})`; custom pset names work via `pset.add_pset`/`pset.edit_pset` (verified).
- `create_entity` does NOT auto-generate `GlobalId` — types are created via `root.create_entity` which does.
- Operator contract: `class MATERIALSDB_OT_apply_push(bpy.types.Operator, tool.Ifc.Operator)`, implement `_execute` (base `execute()` is final and always returns `{"FINISHED"}` after `IfcStore.execute_ifc_operator`); `bl_options = {"REGISTER", "UNDO"}`.
- Upsert semantics: type elements are created-if-missing/kept; re-send rebuilds the EXISTING `IfcMaterialLayerSet` in place (same entity, old layers removed); generic usage → all three types sharing ONE set via one `material.assign_material(products=[all])`.
- Payload entry shapes are frozen: materials `{source_id, layer_id, name, description, category, color, identity, psets, layer}` (slice 1); construction layers `{material_id, thickness_m, material: {source_id, name, description, category, color, identity}}`.
- `bonsai_addon/` excluded from ty (do not add to `tool.ty.src.include`); ruff lints it (path already includes `bonsai_addon`).
- Untracked scratch files at repo root (`temp.py`, `check_XMLVer.py`, `examples/get_material.py`, …) are NEVER added/fixed.

---

### Task 1: Server — `build_add_construction_payload` + action routing

**Files:**
- Modify: `src/materialsdb/gui/listener.py`
- Modify: `src/materialsdb/gui/server.py` (`_listener_send`)
- Test: `tests/test_listener_payload.py`, `tests/test_gui_server.py`

**Interfaces:**
- Consumes: `construction.validate_construction(body, store_) -> (Construction, problems)`; `store_.get/get_summary`; `utils.get_material_name(material, lang)`; `utils.get_material_description(material, lang)`; existing `build_add_materials_payload`.
- Produces: `build_add_construction_payload(store_, body, country=None, lang=None) -> tuple[dict | None, list[str]]` returning `({"action": "add_construction", "construction": {name, design_usage, types, layers}}, [])` or `(None, problems)`. `types`: wall→`["IfcWallType"]`, floor→`["IfcSlabType"]`, roof→`["IfcRoofType"]`, anything else→`["IfcWallType", "IfcSlabType", "IfcRoofType"]`. Send response gains `summary` (string) alongside the existing int `queued` (picker JS updated in Task 5).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_listener_payload.py`:

```python
def test_build_add_construction_types_mapping(store):
    from materialsdb.gui.listener import build_add_construction_payload

    base = {"layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}]}
    payload, problems = build_add_construction_payload(store_, body={**base, "name": "wall one", "design_usage": "consDesignForWall"})
    assert problems == []
    assert payload["construction"]["types"] == ["IfcWallType"]
    payload, problems = build_add_construction_payload(store_, body={**base, "name": "floor one", "design_usage": "consDesignForFloor"})
    assert payload["construction"]["types"] == ["IfcSlabType"]
    payload, problems = build_add_construction_payload(store_, body={**base, "name": "roof one", "design_usage": "consDesignForRoof"})
    assert payload["construction"]["types"] == ["IfcRoofType"]
    payload, problems = build_add_construction_payload(store_, body={**base, "name": "generic one", "design_usage": None})
    assert payload["construction"]["types"] == ["IfcWallType", "IfcSlabType", "IfcRoofType"]
    payload, problems = build_add_construction_payload(store_, body={**base, "name": "odd one", "design_usage": "garbage"})
    assert payload["construction"]["types"] == ["IfcWallType", "IfcSlabType", "IfcRoofType"]
```

Additional tests for the same file:

```python
def test_build_add_construction_payload_shape(store):
    from materialsdb.gui.listener import build_add_construction_payload

    payload, problems = build_add_construction_payload(
        store_,
        body={
            "name": "Mur 20+16",
            "design_usage": "consDesignForWall",
            "layers": [
                {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.22},  # custom thickness
                {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            ],
        },
    )

    assert problems == []
    construction = payload["construction"]
    assert construction["name"] == "Mur 20+16"
    assert construction["design_usage"] == "consDesignForWall"
    assert [layer["thickness_m"] for layer in construction["layers"]] == [0.22, 0.15]  # construction thickness, not manufacturer
    first = construction["layers"][0]["material"]
    assert first["name"] == "Isolant A"
    assert first["category"] == "Insulation"
    assert first["identity"] == {
        "material_id": "00000000-0000-0000-0000-000000000001",
        "company_id": "A1B85A67-5B1E-4960-A297-2DE8275049C5",
        "company": "Mini SA",
    }
    assert "psets" not in first  # minimal to_ifc_layer_set-style material


def test_build_add_construction_reports_problems(store):
    from materialsdb.gui.listener import build_add_construction_payload

    payload, problems = build_add_construction_payload(store_, body={"name": "", "layers": []})

    assert payload is None
    assert "name required" in problems
    assert "at least one layer required" in problems


def test_build_add_construction_unknown_material(store):
    from materialsdb.gui.listener import build_add_construction_payload

    payload, problems = build_add_construction_payload(
        store_, body={"name": "x", "layers": [{"material_id": "nope", "thickness_m": 0.1}]}
    )

    assert payload is None
    assert any("nope" in problem for problem in problems)
```

Append to `tests/test_gui_server.py` (roundtrip area):

```python
def test_listener_send_add_construction(api):
    server, state = api
    status, _ = request(
        server, "POST", "/api/listener/register",
        payload={"client_id": "c1", "model_path": "/tmp/m.ifc"}, token=state.token,
    )
    assert status == 200

    status, body = request(
        server, "POST", "/api/listener/send",
        payload={
            "client_id": "c1",
            "action": "add_construction",
            "construction": {
                "name": "Mur 20+16",
                "design_usage": "consDesignForWall",
                "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}],
            },
        },
        token=state.token,
    )
    assert status == 200
    assert body["queued"] == 1  # layers count
    assert "Mur 20+16" in body["summary"]

    status, body = request(server, "GET", "/api/listener/poll?client_id=c1", token=state.token)
    assert status == 200
    assert body["payload"]["action"] == "add_construction"
    assert body["payload"]["construction"]["types"] == ["IfcWallType"]


def test_listener_send_add_construction_invalid(api):
    server, state = api
    request(server, "POST", "/api/listener/register", payload={"client_id": "c1", "model_path": ""}, token=state.token)
    status, body = request(
        server, "POST", "/api/listener/send",
        payload={"client_id": "c1", "action": "add_construction", "construction": {"name": ""}},
        token=state.token,
    )
    assert status == 400
    assert "name required" in body["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py tests/test_gui_server.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_add_construction_payload'` and 404/`unsupported action` on the new send action.

- [ ] **Step 3: Implement the resolver and routing**

Append to `src/materialsdb/gui/listener.py`:

```python
_DESIGN_USAGE_TO_TYPES = {
    "consDesignForWall": ["IfcWallType"],
    "consDesignForFloor": ["IfcSlabType"],
    "consDesignForRoof": ["IfcRoofType"],
}
GENERIC_TYPES = ["IfcWallType", "IfcSlabType", "IfcRoofType"]


def build_add_construction_payload(store_, body, country=None, lang=None):
    """Resolve a construction body into a self-sufficient add_construction
    payload (or (None, problems) when validation fails). Materials travel
    minimal — identity/name/category/color, to_ifc_layer_set-style — and
    find-or-create on the add-on side keys on the identity pset."""
    from materialsdb import construction as cm

    construction, problems = cm.validate_construction(body, store_)
    if problems:
        return None, problems
    country = country or config.get_country()
    lang = lang or config.get_lang()
    layers = []
    for layer in construction.layers:
        summary = store_.get_summary(layer.material_id)
        material = store_.get(layer.material_id)
        if summary is None or material is None:
            problems.append(f"unknown material id: {layer.material_id}")
            continue
        color = getattr(material.information, "color", None)
        layers.append(
            {
                "material_id": layer.material_id,
                "thickness_m": layer.thickness_m,
                "material": {
                    "source_id": layer.material_id,
                    "name": str(utils.get_material_name(material, lang)),
                    "description": str(utils.get_material_description(material, lang)),
                    "category": str(getattr(material.information, "group", "") or ""),
                    "color": int(color) if color else None,
                    "identity": {
                        "material_id": layer.material_id,
                        "company_id": str(summary.company_id or ""),
                        "company": str(summary.company or ""),
                    },
                },
            }
        )
    if problems:
        return None, problems
    return {
        "action": "add_construction",
        "construction": {
            "name": construction.name,
            "design_usage": construction.design_usage,
            "types": _DESIGN_USAGE_TO_TYPES.get(construction.design_usage or "", GENERIC_TYPES),
            "layers": layers,
        },
    }, []
```

Replace the body of `_listener_send` in `src/materialsdb/gui/server.py` with:

```python
    def _listener_send(self, store_, payload):
        client_id = str(payload.get("client_id") or "")
        listener = self.state.listeners.get(client_id)
        if listener is None:
            self._send(404, {"error": f"unknown listener: {client_id}"})
            return
        action = str(payload.get("action") or "add_materials")
        if action == "add_materials":
            from materialsdb.gui.listener import build_add_materials_payload

            resolved, missing = build_add_materials_payload(store_, payload.get("items") or [])
            if not resolved["materials"]:
                self._send(400, {"error": "nothing resolvable to send", "missing": missing})
                return
            queued = len(resolved["materials"])
            summary = f"{queued} material(s)"
        elif action == "add_construction":
            from materialsdb.gui.listener import build_add_construction_payload

            resolved, problems = build_add_construction_payload(store_, payload.get("construction"))
            if resolved is None:
                self._send(400, {"error": "; ".join(problems)})
                return
            queued = len(resolved["construction"]["layers"])
            summary = f"construction '{resolved['construction']['name']}' ({queued} layer(s))"
        else:
            self._send(400, {"error": f"unsupported action: {action}"})
            return
        listener["pending"] = resolved
        self._send(200, {"ok": True, "queued": queued, "summary": summary})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py tests/test_gui_server.py -q`
Expected: PASS (existing `queued == 2` materials assertion unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/listener.py src/materialsdb/gui/server.py tests/test_listener_payload.py tests/test_gui_server.py
git commit -m "feat(gui): resolve add_construction payloads with design_usage type mapping"
```

---

### Task 2: `insert.py` — rework `apply_add_materials` on `ifcopenshell.api`

**Files:**
- Modify: `bonsai_addon/insert.py` (full rewrite of entity creation; keep function names/shapes)
- Test: `tests/test_bonsai_insert.py` (assertions updated; new style/globalid assertions)

**Interfaces:**
- Consumes: payload entry shape (frozen, see Global Constraints).
- Produces (used by Task 3/4): `apply_add_materials(file, payload) -> int` (unchanged signature); NEW helpers `existing_materials_by_id(file) -> dict[str, entity]` (material_id → first IfcMaterial with matching `materialsdb` pset), `_add_identity_pset(file, material, identity)`, `_apply_style(file, entry_material_dict, material)`; `_existing_keys`, `_pset_props`, `_materials_of` kept.

- [ ] **Step 1: Update the tests**

In `tests/test_bonsai_insert.py`, add one characterization test for the api-based rewrite:

```python
def test_apply_add_materials_creates_schema_valid_entities(store):
    file = ifcopenshell.file(schema="IFC4")

    apply_add_materials(file, _payload(store))

    materials = file.by_type("IfcMaterial")
    assert len(materials) == 2
    for material in materials:
        assert material.Name == "Isolant A"
        assert material.Category == "Insulation"
        assert material.Description == "Panneau isolant"
    assert len(file.by_type("IfcMaterialLayer")) == 2
    assert len(file.by_type("IfcMaterialLayerSet")) == 2
    identity = [p for p in file.by_type("IfcMaterialProperties") if p.Name == "materialsdb"]
    assert len(identity) == 2
```

- [ ] **Step 2: Run tests to verify they pass ALREADY (characterization) then rewrite**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: PASS (current raw-create code satisfies these assertions). This is the safety net for the rewrite.

- [ ] **Step 3: Rewrite the creation path on api**

Replace `bonsai_addon/insert.py` with:

```python
"""Map resolved payloads onto an ifcopenshell file via ifcopenshell.api.

Pure ifcopenshell (no bpy/bonsai imports) so CI can test it directly.
api handles GlobalId/OwnerHistory and keeps entities schema-valid; kwargs
target the 0.9 api (add_layer takes no thickness — edit_layer sets it;
root.create_entity takes ifc_class=)."""

import ifcopenshell.api


def _materials_of(pset):
    materials = pset.Material
    if not isinstance(materials, (list, tuple)):
        materials = [materials]
    return materials


def _pset_props(pset):
    return {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}


def existing_materials_by_id(file):
    """material_id -> first IfcMaterial carrying a matching materialsdb pset."""
    found = {}
    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != "materialsdb":
            continue
        material_id = _pset_props(pset).get("material_id")
        for material in _materials_of(pset):
            found.setdefault(material_id, material)
    return found


def _existing_keys(file):
    """(material_id, layer_id | None) pairs already present, from the
    materialsdb identity + org_layer psets."""
    layer_of = {}
    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != "materialsdb.org_layer":
            continue
        layer_id = _pset_props(pset).get("layer_id")
        for material in _materials_of(pset):
            layer_of[material.id()] = layer_id
    keys = set()
    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != "materialsdb":
            continue
        material_id = _pset_props(pset).get("material_id")
        for material in _materials_of(pset):
            keys.add((material_id, layer_of.get(material.id())))
    return keys


def _add_identity_pset(file, material, identity):
    pset = ifcopenshell.api.run("pset.add_pset", file, product=material, name="materialsdb")
    ifcopenshell.api.run(
        "pset.edit_pset",
        file,
        pset=pset,
        properties={
            "material_id": identity["material_id"],
            "company_id": identity.get("company_id") or "",
            "company": identity.get("company") or "",
        },
    )


def _add_property_psets(file, material, psets):
    for pset_name, props in (psets or {}).items():
        pset = ifcopenshell.api.run("pset.add_pset", file, product=material, name=str(pset_name))
        ifcopenshell.api.run("pset.edit_pset", file, pset=pset, properties=props)


def _apply_style(file, material_entry, material):
    """Best-effort schema-valid material styling; needs a representation
    context (bonsai files have one, scratch CI files do not)."""
    color = material_entry.get("color")
    if not color:
        return
    try:
        contexts = file.by_type("IfcRepresentationContext")
        if not contexts:
            return
        color = int(color)
        name = f"color {color}"
        styles = {style.Name: style for style in file.by_type("IfcSurfaceStyle")}
        style = styles.get(name)
        if style is None:
            style = ifcopenshell.api.run("style.add_style", file, name=name)
            ifcopenshell.api.run(
                "style.add_surface_style",
                file,
                style=style,
                ifc_class="IfcSurfaceStyleShading",
                attributes={
                    "SurfaceColour": file.create_entity(
                        "IfcColourRgb",
                        Name=None,
                        Red=(color >> 16) / 255,
                        Green=((color >> 8) & 255) / 255,
                        Blue=(color & 255) / 255,
                    )
                },
            )
        ifcopenshell.api.run(
            "style.assign_material_style", file, material=material, style=style, context=contexts[0]
        )
    except Exception:  # noqa: BLE001, S110 - cosmetic; never fail the insert
        pass


def apply_add_materials(file, payload) -> int:
    """Create IfcMaterial (+ identity/property psets, layer + set, style) per
    payload entry. Entries whose (material_id, layer_id) key already exists
    are skipped. Returns the number created."""
    existing = _existing_keys(file)
    created = 0
    for entry in payload.get("materials") or []:
        identity = entry["identity"]
        layer = entry.get("layer") or {}
        org_layer_id = (entry.get("psets") or {}).get("materialsdb.org_layer", {}).get("layer_id")
        key = (identity["material_id"], layer.get("layer_id") or org_layer_id)
        if key in existing:
            continue
        material = ifcopenshell.api.run(
            "material.add_material",
            file,
            name=str(entry["name"]),
            category=str(entry.get("category") or ""),
            description=str(entry.get("description") or ""),
        )
        _add_identity_pset(file, material, identity)
        _add_property_psets(file, material, entry.get("psets"))
        if layer:
            thickness = float(layer["thick_m"])
            label = f"{entry['name']} | {round(thickness * 1000)}mm"
            layer_set = ifcopenshell.api.run(
                "material.add_material_set", file, name=label, set_type="IfcMaterialLayerSet"
            )
            ifc_layer = ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=material)
            ifcopenshell.api.run(
                "material.edit_layer",
                file,
                layer=ifc_layer,
                attributes={"LayerThickness": thickness, "Name": label, "Description": str(layer["layer_id"])},
            )
        _apply_style(file, entry, material)
        existing.add(key)
        created += 1
    return created
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py tests/test_bonsai_discovery.py -q`
Expected: PASS (all existing assertions hold under the api-based path).

- [ ] **Step 5: Commit**

```bash
git add bonsai_addon/insert.py tests/test_bonsai_insert.py
git commit -m "refactor(bonsai): insert materials via ifcopenshell.api"
```

---

### Task 3: `insert.py` — `apply_add_construction` (upsert)

**Files:**
- Modify: `bonsai_addon/insert.py`
- Test: `tests/test_bonsai_insert.py`

**Interfaces:**
- Consumes: Task 2's `existing_materials_by_id`, `_add_identity_pset`, `_apply_style`; payload construction shape from Task 1.
- Produces: `apply_add_construction(file, payload) -> dict` returning `{"types_created": int, "sets_updated": int, "materials_created": int}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bonsai_insert.py`:

```python
CONSTRUCTION_PAYLOAD = {
    "action": "add_construction",
    "construction": {
        "name": "Mur 20+16",
        "design_usage": "consDesignForWall",
        "types": ["IfcWallType"],
        "layers": [
            {
                "material_id": "00000000-0000-0000-0000-000000000001",
                "thickness_m": 0.22,
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
                "material_id": "00000000-0000-0000-0000-000000000002",
                "thickness_m": 0.15,
                "material": {
                    "source_id": "00000000-0000-0000-0000-000000000002",
                    "name": "Beton B",
                    "description": "",
                    "category": "Concrete",
                    "color": None,
                    "identity": {
                        "material_id": "00000000-0000-0000-0000-000000000002",
                        "company_id": "A1B85A67-5B1E-4960-A297-2DE8275049C5",
                        "company": "Mini SA",
                    },
                },
            },
        ],
    },
}


def _construction_payload(**overrides):
    import copy

    payload = copy.deepcopy(CONSTRUCTION_PAYLOAD)
    payload["construction"].update(overrides)
    return payload


def test_construction_creates_type_and_layers():
    file = ifcopenshell.file(schema="IFC4")

    summary = apply_add_construction(file, CONSTRUCTION_PAYLOAD)

    assert summary == {"types_created": 1, "sets_updated": 0, "materials_created": 2}
    types = file.by_type("IfcWallType")
    assert len(types) == 1
    wall_type = types[0]
    assert wall_type.Name == "Mur 20+16"
    assert wall_type.GlobalId  # api root.create_entity generates it
    sets = file.by_type("IfcMaterialLayerSet")
    assert len(sets) == 1
    layers = sorted(sets[0].MaterialLayers, key=lambda l: l.LayerThickness)
    assert [round(l.LayerThickness, 3) for l in layers] == [0.15, 0.22]
    assert {l.Description for l in layers} == {
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    }
    associations = [a for a in wall_type.HasAssociations if a.is_a("IfcRelAssociatesMaterial")]
    assert len(associations) == 1
    assert associations[0].RelatingMaterial == sets[0]


def test_construction_resend_updates_same_set():
    file = ifcopenshell.file(schema="IFC4")
    apply_add_construction(file, CONSTRUCTION_PAYLOAD)
    set_before = file.by_type("IfcMaterialLayerSet")[0].id()

    modified = _construction_payload()
    modified["construction"]["layers"][0]["thickness_m"] = 0.25
    summary = apply_add_construction(file, modified)

    assert summary == {"types_created": 0, "sets_updated": 1, "materials_created": 0}
    assert len(file.by_type("IfcWallType")) == 1  # no duplicate type
    assert len(file.by_type("IfcMaterialLayerSet")) == 1  # same set entity, modified in place
    assert file.by_type("IfcMaterialLayerSet")[0].id() == set_before
    layers = file.by_type("IfcMaterialLayerSet")[0].MaterialLayers
    assert {round(l.LayerThickness, 3) for l in layers} == {0.25, 0.15}


def test_construction_generic_three_types_shared_set():
    payload = _construction_payload(design_usage=None, types=["IfcWallType", "IfcSlabType", "IfcRoofType"])
    file = ifcopenshell.file(schema="IFC4")

    summary = apply_add_construction(file, payload)

    assert summary["types_created"] == 3
    assert len(file.by_type("IfcWallType")) == 1
    assert len(file.by_type("IfcSlabType")) == 1
    assert len(file.by_type("IfcRoofType")) == 1
    assert len(file.by_type("IfcMaterialLayerSet")) == 1  # one shared set
    relations = [a for a in file.by_type("IfcRelAssociatesMaterial")]
    assert len(relations) == 1
    assert len(relations[0].RelatedObjects) == 3


def test_construction_reuses_preset_materials(store):
    file = ifcopenshell.file(schema="IFC4")
    apply_add_materials(file, _payload(store))  # materials already in the model

    summary = apply_add_construction(file, CONSTRUCTION_PAYLOAD)

    assert summary["materials_created"] == 0  # both found via identity pset
    materials = file.by_type("IfcMaterial")
    assert len(materials) == 3  # 2 from apply_add_materials (Isolant A x2 layers) + Beton B
```

Also add to the imports at the top of the test file (after the existing `from insert import apply_add_materials`):

```python
from insert import apply_add_construction  # ty: ignore[unresolved-import] - add-on module on a side path
```

(keep the existing `# ty: ignore[unresolved-import]` comment style on that line).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: FAIL — `ImportError: cannot import name 'apply_add_construction'`.

- [ ] **Step 3: Implement `apply_add_construction`**

Append to `bonsai_addon/insert.py`:

```python
def apply_add_construction(file, payload) -> dict:
    """Upsert a construction as typed element(s) + IfcMaterialLayerSet.

    Types are created if missing and kept if present; a re-send rebuilds the
    existing set IN PLACE (same entity, old layers removed) and re-assigns
    all targets onto it. Generic usage shares ONE set across all types via a
    single IfcRelAssociatesMaterial. Materials are found by their materialsdb
    identity pset or created minimally (identity + style, no per-layer
    psets)."""
    construction = payload["construction"]
    summary = {"types_created": 0, "sets_updated": 0, "materials_created": 0}

    known = existing_materials_by_id(file)
    layers = []
    for layer in construction["layers"]:
        material = known.get(layer["material_id"])
        if material is None:
            entry = layer["material"]
            material = ifcopenshell.api.run(
                "material.add_material",
                file,
                name=str(entry["name"]),
                category=str(entry.get("category") or ""),
                description=str(entry.get("description") or ""),
            )
            _add_identity_pset(file, material, entry["identity"])
            _apply_style(file, entry, material)
            known[layer["material_id"]] = material
            summary["materials_created"] += 1
        layers.append((layer, material))

    name = str(construction["name"])
    targets = []
    for cls in construction["types"]:
        match = [t for t in file.by_type(cls) if t.Name == name]
        if match:
            targets.append(match[0])
        else:
            targets.append(ifcopenshell.api.run("root.create_entity", file, ifc_class=cls, name=name))
            summary["types_created"] += 1

    the_set = None
    for target in targets:
        for association in target.HasAssociations or ():
            relating = getattr(association, "RelatingMaterial", None)
            if relating is not None and relating.is_a("IfcMaterialLayerSet"):
                the_set = relating
                break
        if the_set is not None:
            break

    if the_set is None:
        the_set = ifcopenshell.api.run(
            "material.add_material_set", file, name=name, set_type="IfcMaterialLayerSet"
        )
    else:
        summary["sets_updated"] += 1
        for stale in list(the_set.MaterialLayers or ()):
            ifcopenshell.api.run("material.remove_layer", file, layer=stale)

    for layer, material in layers:
        ifc_layer = ifcopenshell.api.run("material.add_layer", file, layer_set=the_set, material=material)
        mm = round(float(layer["thickness_m"]) * 1000)
        ifcopenshell.api.run(
            "material.edit_layer",
            file,
            layer=ifc_layer,
            attributes={
                "LayerThickness": float(layer["thickness_m"]),
                "Name": f"{layer['material']['name']} | {mm}mm",
                "Description": str(layer["material_id"]),
            },
        )

    for target in targets:
        for association in list(target.HasAssociations or ()):
            file.remove(association)
    ifcopenshell.api.run(
        "material.assign_material",
        file,
        products=[t for t in targets if t is not None],
        type="IfcMaterialLayerSet",
        material=the_set,
    )
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: PASS (all 8 tests).

- [ ] **Step 5: Commit**

```bash
git add bonsai_addon/insert.py tests/test_bonsai_insert.py
git commit -m "feat(bonsai): upsert constructions as typed elements via ifcopenshell.api"
```

---

### Task 4: Add-on — undoable push operator + timer dispatch

**Files:**
- Modify: `bonsai_addon/__init__.py`
- Test: `bonsai_addon/test/test_in_blender.py` (existing roundtrip now exercises the operator; adjusted in Task 6)

**Interfaces:**
- Consumes: `insert.apply_add_materials`, `insert.apply_add_construction` (Tasks 2-3); `tool.Ifc.Operator` base (bonsai) whose final `execute()` calls `IfcStore.execute_ifc_operator`.
- Produces: operator `bpy.ops.materialsdb.apply_push()`; module-global `_PENDING` handoff (`_PENDING = payload` before invoking the op). Task 6's tests drive `_poll_timer()` and assert undo/redo.

- [ ] **Step 1: Implement the operator and rewire the timer**

In `bonsai_addon/__init__.py`:

After `_CLIENT = None`, add:

```python
_PENDING = None


class MATERIALSDB_OT_apply_push(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "materialsdb.apply_push"
    bl_label = "materialsdb apply push"
    bl_description = "Apply a pushed materialsdb payload to the open IFC model (undoable)"
    bl_options: typing.ClassVar[set[str]] = {"REGISTER", "UNDO"}

    def _execute(self, context):
        global _PENDING
        payload, _PENDING = _PENDING, None
        if payload is None:
            return
        action = payload.get("action")
        if action == "add_materials":
            count = insert.apply_add_materials(tool.Ifc.get(), payload)
            _CLIENT.report("applied", f"{count} material(s) added")
        elif action == "add_construction":
            result = insert.apply_add_construction(tool.Ifc.get(), payload)
            _CLIENT.report(
                "applied",
                f"{result['types_created']} type(s) created, {result['sets_updated']} set(s) updated, "
                f"{result['materials_created']} material(s) created",
            )
        else:
            _CLIENT.report("error", f"unknown action: {action}")
```

Replace the insert block inside `_poll_timer` (the `payload = _CLIENT.poll()` … `insert.apply_add_materials(...)` … `_CLIENT.report(...)` region INCLUDING the guarded `handler.refresh_ui_data()` block — refresh is now handled by `execute_ifc_operator`) with:

```python
        payload = _CLIENT.poll()
        if payload is not None:
            global _PENDING
            _PENDING = payload
            try:
                bpy.ops.materialsdb.apply_push()
            except Exception as err:  # noqa: BLE001 - report; never kill the poll loop
                try:
                    _CLIENT.report("error", str(err))
                except Exception:  # noqa: BLE001, S110
                    pass
```

(The `global _PENDING` declaration must appear at the top of `_poll_timer` alongside the existing `global _CLIENT`.)

Update `register()`/`unregister()` to add/remove the new operator class:

```python
def register():
    bpy.utils.register_class(MATERIALSDB_OT_toggle_listener)
    bpy.utils.register_class(MATERIALSDB_OT_apply_push)
    bpy.utils.register_class(MATERIALSDB_PT_panel)


def unregister():
    global _CLIENT
    _CLIENT = None
    if bpy.app.timers.is_registered(_poll_timer):
        bpy.app.timers.unregister(_poll_timer)
    bpy.utils.unregister_class(MATERIALSDB_PT_panel)
    bpy.utils.unregister_class(MATERIALSDB_OT_toggle_listener)
    bpy.utils.unregister_class(MATERIALSDB_OT_apply_push)
```

Remove the now-unused `from bonsai.bim import handler` import and the `_active_ifc_file` helper (both replaced by `tool.Ifc.get()` inside the operator). Keep `_model_path` (registration still uses it).

- [ ] **Step 2: Lint and syntax check**

Run: `ruff check --exclude src/materialsdb/classes.py bonsai_addon && ruff format --exclude src/materialsdb/classes.py --check . && python3 -m pytest -p no:pytest-blender -q && ty check --project .`
Expected: all clean (main suite unaffected — bpy code is ty/ruff-visible only).

- [ ] **Step 3: Commit**

```bash
git add bonsai_addon/__init__.py
git commit -m "feat(bonsai): undoable push operator dispatching on action"
```

---

### Task 5: GUI — shared target poll + constructions send button

**Files:**
- Modify: `src/materialsdb/gui/static/picker-core.js`
- Modify: `src/materialsdb/gui/static/app.js`
- Modify: `src/materialsdb/gui/static/constructions.html`
- Modify: `src/materialsdb/gui/static/app-constructions.js`
- Test: `tests/harness/picker_core_harness.mjs` (extend), `tests/test_picker_frontend.py` (unchanged runner)

**Interfaces:**
- Consumes: `GET /api/listener/clients`; server `summary` field from Task 1.
- Produces: `PickerCore.startTargetPoll(apiFn, select, button) -> {refresh, stop, current()}`; constructions page sends `{client_id, action: "add_construction", construction: {name, design_usage, layers: [{material_id, thickness_m}]}}`.

- [ ] **Step 1: Add `startTargetPoll` to picker-core.js**

Inside the `PickerCore` IIFE (before the `return`), add:

```js
  function startTargetPoll(apiFn, select, button) {
    let clients = [];
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
        const label = c.model_path ? c.model_path.split(/[\\/]/).pop() : "Bonsai";
        const mark = c.last_status ? (c.last_status.status === "applied" ? " \u2713" : " \u2717") : "";
        return `<option value="${esc(c.client_id)}">${esc(label)}${mark}</option>`;
      }).join("");
      if (clients.some((c) => c.client_id === previous)) select.value = previous;
    };
    refresh();
    const timer = setInterval(refresh, 2000);
    return {
      refresh,
      stop: () => clearInterval(timer),
      current: () => select.value || (clients[0] ? clients[0].client_id : null) || null,
    };
  }
```

Extend the return object with `startTargetPoll`.

- [ ] **Step 2: Refactor app.js to use it**

Replace `refreshBonsaiClients` + `let bonsaiClients = []` + the `setInterval(refreshBonsaiClients, 2000); refreshBonsaiClients();` lines with a single wiring line placed where the interval was:

```javascript
const bonsaiTarget = PickerCore.startTargetPoll(api, $("bonsai-target"), $("send-bonsai"));
```

In `sendToBonsai`, replace the client resolution and status line:

```javascript
  const client_id = bonsaiTarget.current();
  if (!client_id) return setStatus("no Bonsai listener connected");
```

and

```javascript
  setStatus(`sent to Bonsai: ${result.summary}`);
```

- [ ] **Step 3: Constructions page controls**

In `constructions.html`, after the `<button id="append-session">append to session</button>` line (line 84), add:

```html
  <select id="bonsai-target" style="display:none"></select>
  <button id="send-bonsai" disabled>send to Bonsai</button>
```

In `app-constructions.js`, add near the other handlers (after the `$("save").onclick` block):

```javascript
const bonsaiTarget = PickerCore.startTargetPoll(api, $("bonsai-target"), $("send-bonsai"));

$("send-bonsai").onclick = async () => {
  if (!layers.length) return setStatus("add at least one layer");
  if (!$("name").value.trim()) return setStatus("name the construction before sending");
  const client_id = bonsaiTarget.current();
  if (!client_id) return setStatus("no Bonsai listener connected");
  const result = await api("/api/listener/send", {
    method: "POST",
    body: JSON.stringify({
      client_id,
      action: "add_construction",
      construction: {
        name: $("name").value.trim(),
        design_usage: $("design-usage").value || null,
        layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })),
      },
    }),
  });
  setStatus(`sent to Bonsai: ${result.summary}`);
};
```

If `tests/harness/construction_page_harness.mjs` executes `app-constructions.js` top-level code, add a `PickerCore` stub to its VM context (e.g. `PickerCore: {esc: (v) => v, startTargetPoll: () => ({current: () => null})}`) so the harness keeps passing — run it to check.

- [ ] **Step 4: Verify**

Run: `node --check src/materialsdb/gui/static/app.js && node --check src/materialsdb/gui/static/picker-core.js && node --check src/materialsdb/gui/static/app-constructions.js && node tests/harness/picker_core_harness.mjs src/materialsdb/gui/static/picker-core.js && python3 -m pytest -p no:pytest-blender tests/test_picker_frontend.py tests/test_construction_frontend.py -q`
Expected: all pass.

Run: `ruff format --exclude src/materialsdb/classes.py --check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/static/picker-core.js src/materialsdb/gui/static/app.js src/materialsdb/gui/static/constructions.html src/materialsdb/gui/static/app-constructions.js tests/harness/construction_page_harness.mjs
git commit -m "feat(gui): send constructions to Bonsai via shared target poll"
```

---

### Task 6: In-Blender harness — operator path, construction roundtrip, undo

**Files:**
- Modify: `bonsai_addon/test/conftest.py` (register add-on classes once)
- Modify: `bonsai_addon/test/test_in_blender.py` (roundtrip detail assert; new construction + undo tests)

**Interfaces:**
- Consumes: Task 1 payload (`add_construction`), Task 4 operator, `_poll_timer()` via `bpy.ops.materialsdb.apply_push()`.

- [ ] **Step 1: Register the add-on in the harness**

Append to `bonsai_addon/test/conftest.py` (after the `fresh_project` fixture):

```python
@pytest.fixture(scope="session", autouse=True)
def registered_addon():
    import bonsai_addon

    try:
        bonsai_addon.register()
    except Exception as err:  # noqa: BLE001 - already registered in this Blender session
        print(f"add-on register skipped: {err}")
    yield
```

- [ ] **Step 2: Update + extend the tests**

In `bonsai_addon/test/test_in_blender.py`:

1. In `test_full_listener_roundtrip`, replace the final clients assertion with the exact detail:

```python
        status, body = _request(port, token, "GET", "/api/listener/clients")
        assert status == 200
        assert body["clients"][0]["last_status"] == {"status": "applied", "detail": "2 material(s) added"}
```

2. Add the construction roundtrip + undo test, and refactor `test_full_listener_roundtrip` to use the same `_seeded_server`/`_stop_server` helpers (deduplicating its inline server setup):

```python
def _seeded_server(tmp_path):
    cache_dir = tmp_path / "cache"
    old_cache_env = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = str(cache_dir)
    python3 = shutil.which("python3")
    assert python3, "system python3 with lxml required to host the server subprocess"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    server = subprocess.Popen(
        [python3, "-m", "materialsdb.gui", "--no-browser", "--port", "0"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    info = _wait_for_gui_info(cache_dir)
    producers = cache_dir / "materialsdb" / "Producers"
    producers.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "mini_producer.xml", producers / "mini_producer.xml")
    status, _ = _request(info["port"], info["token"], "POST", "/api/refresh", {})
    assert status == 200
    return server, cache_dir, info, old_cache_env


def _stop_server(server, old_cache_env):
    server.terminate()
    server.wait(timeout=10)
    if old_cache_env is None:
        os.environ.pop("XDG_CACHE_HOME", None)
    else:
        os.environ["XDG_CACHE_HOME"] = old_cache_env


def test_construction_roundtrip_and_undo(tmp_path):
    """Construction push lands an IfcWallType + layer set in the live model,
    a re-send modifies the SAME set in place, and Blender undo/redo works."""
    import bpy

    server, cache_dir, info, old_cache_env = _seeded_server(tmp_path)
    try:
        port, token = info["port"], info["token"]
        client = ListenerClient()
        client.register(bonsai_addon._model_path())

        def send(construction):
            status, body = _request(
                port,
                token,
                "POST",
                "/api/listener/send",
                {"client_id": client.client_id, "action": "add_construction", "construction": construction},
            )
            assert status == 200, body

        construction = {
            "name": "Mur 20+16",
            "design_usage": "consDesignForWall",
            "layers": [
                {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.22},
                {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            ],
        }
        send(construction)

        previous = bonsai_addon._CLIENT
        bonsai_addon._CLIENT = client
        try:
            assert bonsai_addon._poll_timer() == 1.0
        finally:
            bonsai_addon._CLIENT = previous

        file = tool.Ifc.get()
        assert file is not None
        wall_types = file.by_type("IfcWallType")
        assert len(wall_types) == 1
        assert wall_types[0].Name == "Mur 20+16"
        assert wall_types[0].GlobalId
        layer_set = wall_types[0].HasAssociations[0].RelatingMaterial
        assert layer_set.is_a("IfcMaterialLayerSet")
        assert {round(l.LayerThickness, 3) for l in layer_set.MaterialLayers} == {0.22, 0.15}

        # re-send with a modified thickness: same set entity updated in place
        set_step_id = layer_set.id()
        modified = {
            "name": "Mur 20+16",
            "design_usage": "consDesignForWall",
            "layers": [
                {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.25},
                {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            ],
        }
        send(modified)
        bonsai_addon._CLIENT = client
        try:
            assert bonsai_addon._poll_timer() == 1.0
        finally:
            bonsai_addon._CLIENT = previous
        assert len(file.by_type("IfcWallType")) == 1  # upsert, no duplicate
        assert len(file.by_type("IfcMaterialLayerSet")) == 1
        assert file.by_type("IfcMaterialLayerSet")[0].id() == set_step_id
        assert {round(l.LayerThickness, 3) for l in layer_set.MaterialLayers} == {0.25, 0.15}

        status, body = _request(port, token, "GET", "/api/listener/clients")
        assert body["clients"][0]["last_status"]["status"] == "applied"

        # Blender undo removes the last push; redo restores it
        bpy.ops.ed.undo()
        assert len(file.by_type("IfcMaterialLayerSet")) == 0 or (
            {round(l.LayerThickness, 3) for l in file.by_type("IfcMaterialLayerSet")[0].MaterialLayers} == {0.22, 0.15}
        )
        bpy.ops.ed.redo()
        assert {round(l.LayerThickness, 3) for l in file.by_type("IfcMaterialLayerSet")[0].MaterialLayers} == {0.25, 0.15}
    finally:
        _stop_server(server, old_cache_env)
```

NOTE on the undo assertion: after `bpy.ops.ed.undo()` the state must equal the FIRST send (thicknesses {0.22, 0.15}); the first branch (`== 0`) is only reached if bonsai's undo rewound past both pushes — either way the re-send's 0.25 must be gone. If the undo wiring surfaces a different but consistent behavior (e.g. `undo_post` needs a UI-side prop unavailable in background), INVESTIGATE AND FIX THE ADD-ON — the user explicitly requires working Ctrl+Z; do not weaken the test into a tautology without controller approval.

- [ ] **Step 3: Run the harness**

Run: `pytest bonsai_addon/test -q`
Expected: 4 passed (`test_insert_into_bonsai_project`, `test_full_listener_roundtrip`, `test_construction_roundtrip_and_undo`, plus any previously passing).

If undo does not revert: debug the add-on (likely `props.last_transaction` wiring in `execute_ifc_operator` vs `undo_post` in background mode) — try driving the undo through bonsai's own expectations (e.g. `bpy.context.window_manager` undo push or setting `tool.Blender.get_bim_props().last_transaction`) until `bpy.ops.ed.undo()` actually rewinds IFC state. Report the root cause in the task report.

- [ ] **Step 4: Commit**

```bash
git add bonsai_addon/test/conftest.py bonsai_addon/test/test_in_blender.py
git commit -m "test(bonsai): construction roundtrip and undo coverage in real Blender"
```

---

### Task 7: Docs + full gates + zip

**Files:**
- Modify: `README.md` (constructions section gains one sentence)

- [ ] **Step 1: README**

In the "Construction maker :" section, after the export sentence ("…or append them into an already-open session file."), add:

```markdown
You can also push the construction straight into the IFC model open in
Bonsai (same target picker as the materials page): it is created or updated
as an `IfcWallType`/`IfcSlabType`/`IfcRoofType` (per the design usage,
generic creating all three) with its `IfcMaterialLayerSet` — undoable with
Ctrl+Z.
```

- [ ] **Step 2: Full gates**

Run: `python3 -m pytest -p no:pytest-blender -q && ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon && ruff format --exclude src/materialsdb/classes.py --check . && ty check --project . && pytest bonsai_addon/test -q && python3 dev_utils/build_bonsai_addon.py`
Expected: everything green; `dist/materialsdb_listener.zip` rebuilt.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: construction push to Bonsai in the construction maker"
```
