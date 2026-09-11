# Layer Replace + ThermalTransmittance psets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace a layer's material from the construction composer (pencil + double-click), and push the computed U-value into `Pset_*Common.ThermalTransmittance` per element type.

**Architecture:** Construction math gains a per-direction helper (`u_values_by_type`); the push resolver attaches `u_values` to the payload server-side; the add-on writes/deletes type-matched psets on upsert; the composer page gets a per-row replace interaction reusing the existing chooser overlay.

**Tech Stack:** Python (dataclasses, pytest), ifcopenshell api + `ifcopenshell.util.element`, vanilla JS + Node VM harness, Blender in-Blender pytest harness (local only).

**Spec:** `docs/superpowers/specs/2026-09-11-layer-replace-thermal-transmittance-design.md`

## Global Constraints

- Tests: `python3 -m pytest -p no:pytest-blender -q` (full suite); NEVER pass `-p no:pytest-blender` for `tests/bonsai_addon/test` / in-Blender harness — that only runs locally.
- Lint/format: `ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon` then `ruff format --exclude "src/materialsdb/classes.py" .` on touched files at every commit gate.
- Type check: `ty check --project .` on touched Python at every commit gate.
- Entity creation exclusively via `ifcopenshell.api` (0.9 parameter style); reads (psets, element kinds) via `ifcopenshell.util.*`.
- GUI copy is software-agnostic; server owns the U computation (client numbers are never trusted).
- Generated code `src/materialsdb/classes.py` is off-limits.
- Commits: one per task, messages follow repo style (`feat: …` / `test: …` / `docs: …`).

---

### Task 1: `construction.py` — per-direction U helper

**Files:**
- Modify: `src/materialsdb/construction.py` (u_value at lines 89-128 + module head)
- Test: `tests/test_construction.py`

**Interfaces:**
- Consumes: existing `RESISTANCE_PRESETS`, `_DESIGN_USAGE_TO_DIRECTION`, `_direction(design_usage) -> str`, `UResult`.
- Produces: `_u_for_direction(construction, store_, direction, preset="ISO6946") -> UResult` (module-private) and public `u_values_by_type(construction, store_, types, preset="ISO6946") -> dict[str, float | None]` with mapping `IfcWallType→wall`, `IfcSlabType→floor`, `IfcRoofType→roof`; unknown type raises `ValueError`; unresolvable λ → `None` per type. `u_value()` public behaviour unchanged.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_construction.py`)

```python
def test_u_values_by_type_generic_maps_per_direction(store):
    construction = make_construction(design_usage=None)
    values = u_values_by_type(construction, store, ["IfcWallType", "IfcSlabType", "IfcRoofType"])

    wall = u_value(make_construction("consDesignForWall"), store)
    floor = u_value(make_construction("consDesignForFloor"), store)
    roof = u_value(make_construction("consDesignForRoof"), store)
    assert values == {
        "IfcWallType": pytest.approx(wall.u),
        "IfcSlabType": pytest.approx(floor.u),
        "IfcRoofType": pytest.approx(roof.u),
    }


def test_u_values_by_type_subset_of_types(store):
    values = u_values_by_type(make_construction(design_usage=None), store, ["IfcRoofType"])

    roof = u_value(make_construction("consDesignForRoof"), store)
    assert values == {"IfcRoofType": pytest.approx(roof.u)}


def test_u_values_by_type_unknown_type_raises(store):
    with pytest.raises(ValueError, match="unknown.*IfcSlabType$".replace("IfcSlabType", "ElementTypes")):
        pytest.fail("placeholder")  # replaced in step 3 — see note
```

Note (fix in this step before running): the third test must be exactly:

```python
def test_u_values_by_type_unknown_type_raises(store):
    with pytest.raises(ValueError, match="unknown element type"):
        u_values_by_type(make_construction(design_usage=None), store, ["IfcWallType", "Ifc CurtainType"])


def test_u_values_by_type_none_when_lambda_unresolvable(store, mixed_xml):
    store.refresh(paths=[_MINI_XML_PATH, mixed_xml])
    construction = Construction(
        name="with btk",
        design_usage=None,
        layers=[ConstructionLayer("00000000-0000-0000-0000-000000000004", thickness_m=0.3)],
    )
    values = u_values_by_type(construction, store, ["IfcWallType"])
    assert values == {"IfcWallType": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_construction.py -q -k u_values_by_type`
Expected: FAIL — `ImportError: cannot import name 'u_values_by_type'` (adjust the import line at the top of the test file to include `u_values_by_type`).

- [ ] **Step 3: Implement**

Refactor `u_value()` + add the helper (replace lines 89-128 of `src/materialsdb/construction.py`; imports unchanged):

```python
def u_value(construction: Construction, store_, preset: str = "ISO6946") -> UResult:
    if preset not in RESISTANCE_PRESETS:
        raise ValueError(f"unknown preset: {preset} (available: {sorted(RESISTANCE_PRESETS)})")
    return _u_for_direction(construction, store_, _direction(construction.design_usage), preset)


def _u_for_direction(
    construction: Construction, store_, direction: str, preset: str = "ISO6946"
) -> UResult:
    if direction not in RESISTANCE_PRESETS[preset]:
        raise ValueError(f"unknown direction: {direction}")
    rsi, rse = RESISTANCE_PRESETS[preset][direction]
    country = config.get_country()

    contributions = []
    missing = []
    r_sum = 0.0
    for index, layer in enumerate(construction.layers):
        if layer.placeholder is not None:
            name = layer.placeholder.get("name") or ""
            lambda_value = finite_or_none(layer.placeholder.get("lambda_value"))
        else:
            summary = store_.get_summary(layer.material_id)
            if summary is not None:
                name = summary.names.get(config.get_lang()) or summary.names.get("") or ""
            lambda_value = resolve_lambda(store_, layer.material_id, country)
        if lambda_value is None or lambda_value <= 0 or not layer.thickness_m or layer.thickness_m <= 0:
            missing.append(layer.material_id)
            continue
        r_layer = layer.thickness_m / lambda_value
        r_sum += r_layer
        contributions.append(
            {
                "material_id": layer.material_id,
                "name": name,
                "d_m": layer.thickness_m,
                "lambda_value": lambda_value,
                "r": r_layer,
                "layer_index": index,
            }
        )

    if missing or not contributions or math.isclose(r_sum, 0):
        return UResult(u=None, rsi=rsi, rse=rse, contributions=contributions, missing_lambda_ids=missing)

    u = 1 / (rsi + r_sum + rse)
    return UResult(u=u, rsi=rsi, rse=rse, contributions=contributions, missing_lambda_ids=missing)


_TYPE_TO_DIRECTION = {
    "IfcWallType": "wall",
    "IfcSlabType": "floor",
    "IfcRoofType": "roof",
}


def u_values_by_type(
    construction: Construction, store_, types, preset: str = "ISO6946"
) -> dict[str, float | None]:
    """U per element class, each with its own heat-flow direction
    (wall / floor / roof Rsi-Rse). Unresolvable λ → None for that type."""
    if preset not in RESISTANCE_PRESETS:
        raise ValueError(f"unknown preset: {preset} (available: {sorted(RESISTANCE_PRESETS)})")
    for cls in types:
        if cls not in _TYPE_TO_DIRECTION:
            raise ValueError(f"unknown element type: {cls}")
    return {
        cls: _u_for_direction(construction, store_, _TYPE_TO_DIRECTION[cls], preset).u
        for cls in types
    }
```

(Function body of the old `u_value` moves verbatim into `_u_for_direction` — only the rsi/rse line changes as shown.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_construction.py -q`
Expected: PASS (new tests + all existing u_value tests).

- [ ] **Step 5: Commit**

```bash
ruff check --exclude src/materialsdb/classes.py src tests && ruff format --exclude "src/materialsdb/classes.py" src tests
ty check --project .
git add src/materialsdb/construction.py tests/test_construction.py
git commit -m "feat(construction): u_values_by_type computes one U per element direction"
```

---

### Task 2: `listener.py` — payload carries `u_values`

**Files:**
- Modify: `src/materialsdb/gui/listener.py` (`build_add_construction_payload`, return dict after the `"construction"` keys — after `"types"` line 145)
- Test: `tests/test_listener_payload.py`

**Interfaces:**
- Consumes: `cm.u_values_by_type(construction, store_, types)` from Task 1; existing `construction`/`types` locals in the payload builder.
- Produces: payload `construction` dict gains key `"u_values": dict[str, float]` — **None entries omitted**; key always present (possibly `{}`). The add-on reads `construction.get("u_values") or {}` (Task 3).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_listener_payload.py`; add `from materialsdb.gui.listener import build_add_construction_payload` to the existing import)

```python
from pytest import approx


def _construction_body(design_usage=None, layers=None):
    return {
        "name": "Test wall",
        "design_usage": design_usage,
        "layers": layers or [
            {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2},
        ],
    }


def test_construction_payload_generic_has_per_type_u_values(store):
    payload, problems = build_add_construction_payload(store, _construction_body())

    assert problems == []
    u_values = payload["construction"]["u_values"]
    assert sorted(u_values) == ["IfcRoofType", "IfcSlabType", "IfcWallType"]
    assert u_values["IfcWallType"] == approx(u_values["IfcWallType"])
    # fewer inner resistances → higher U: floor(Rsi .17) < wall(.13) < roof(.10)
    assert u_values["IfcSlabType"] < u_values["IfcWallType"] < u_values["IfcRoofType"]


def test_construction_payload_explicit_usage_has_one_u_value(store):
    payload, problems = build_add_construction_payload(store, _construction_body(design_usage="consDesignForWall"))

    assert problems == []
    assert payload["construction"]["u_values"] == {"IfcWallType": approx(payload["construction"]["u_values"]["IfcWallType"])}
    assert list(payload["construction"]["u_values"]) == ["IfcWallType"]


def _construction_body_with_missing_lambda():
    return {
        "name": "with btk",
        "design_usage": None,
        "layers": [{"material_id": "00000000-0000-0000-0000-000000000004", "thickness_m": 0.3}],
    }


def test_construction_payload_no_u_values_when_lambda_unresolvable(store):
    # mini_xml fixture lacks material 000000000004: lambda unresolvable → nothing
    payload, problems = build_add_construction_payload(store, _construction_body_with_missing_lambda())

    if problems:  # store dependent: may reject unknown id — accepted either way
        assert payload is None
        return
    assert payload["construction"]["u_values"] == {}
```

(Note: after writing, delete the `if store_` unknown-id ambiguity by checking `mini.xml` supplies material `00000000-0000-0000-0000-000000000004`; if it does NOT, the validator rejects with `unknown material id` and the first branch runs — the test covers the `u_values == {}` path only when the material resolves. If the first branch is impossible for this fixture, replace the early-return with `assert problems == []` — verify against the fixture, pick one branch, delete the other.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py -q -k construction`
Expected: FAIL — `KeyError: 'u_values'`.

- [ ] **Step 3: Implement**

In `build_add_construction_payload`, the return dict gains one key (after `"types": ...`):

```python
    return {
        "action": "add_construction",
        "construction": {
            "name": construction.name,
            "design_usage": construction.design_usage,
            "types": _DESIGN_USAGE_TO_TYPES.get(construction.design_usage or "", GENERIC_TYPES),
            "u_values": {
                cls: u
                for cls, u in cm.u_values_by_type(
                    construction, store_, _DESIGN_USAGE_TO_TYPES.get(construction.design_usage or "", GENERIC_TYPES)
                ).items()
                if u is not None
            },
            "layers": layers,
        },
    }
```

(Keep the exact existing `"types"` expression — compute the types list ONCE in a local `types = _DESIGN_USAGE_TO_TYPES.get(construction.design_usage or "", GENERIC_TYPES)` and use it for both keys.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py -q`
Expected: PASS (all, not just new).

- [ ] **Step 5: Commit**

```bash
ruff check --exclude src/materialsdb/classes.py src tests && ruff format --exclude "src/materialsdb/classes.py" src tests
ty check --project .
git add src/materialsdb/gui/listener.py tests/test_listener_payload.py
git commit -m "feat(listener): add_construction payload carries per-type u_values"
```

---

### Task 3: add-on — write `Pset_*Common.ThermalTransmittance`

**Files:**
- Modify: `bonsai_addon/insert.py` (`apply_add_construction`, ending at the `material.assign_material` call line ~250, plus a module constant near the top imports)
- Modify: `bonsai_addon/__init__.py` (`MATERIALSDB_OT_apply_push._execute` status message, line ~47)
- Modify: `tests/test_bonsai_insert.py` (new tests + adjust exact-summary asserts)
- Test: `tests/test_bonsai_insert.py` (run via the main suite)

**Interfaces:**
- Consumes: payload `construction["u_values"]` (Task 2 format; tolerate absence).
- Produces: `apply_add_construction` summary gains `"psets_written": int`; property `ThermalTransmittance` (IfcReal) on the type product's `Pset_WallCommon`/`Pset_SlabCommon`/`Pset_RoofCommon`; status line mentions the count.

- [ ] **Step 1: Adjust the existing exact-summary asserts**

`grep -n "summary ==" tests/test_bonsai_insert.py` — every exact dict comparison in `test_construction_creates_type_and_layers` and `test_construction_resend_updates_same_set` gains `"psets_written": 0`:

```python
    assert summary == {"types_created": 1, "sets_updated": 0, "materials_created": 2, "placeholders_matched": 0, "psets_written": 0}
```

(and `types_created: 0, sets_updated: 1, materials_created: 0, placeholders_matched: 0, psets_written: 0` for the resend test). Run `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q -k construction` — FAIL (`KeyError: 'psets_written'`).

- [ ] **Step 2: Write the failing tests** (append; uses `ifcopenshell.util.element.get_pset` — the file already imports `ifcopenshell.util.element`)

```python
def _u_values(**values):
    return {"construction": {**_construction_payload()["construction"], "u_values": values}}


def test_construction_walltype_gets_thermal_transmittance():
    file = ifcopenshell.file(schema="IFC4")
    payload = _u_values(**{"IfcWallType": 0.25})

    summary = apply_add_construction(file, payload)

    assert summary["psets_written"] == 1
    wall = file.by_type("IfcWallType")[0]
    props = ifcopenshell.util.element.get_pset(wall, name="Pset_WallCommon")
    assert props is not None and props["ThermalTransmittance"] == pytest.approx(0.25)


def test_construction_generic_types_get_their_own_psets():
    file = ifcopenshell.file(schema="IFC4")
    payload = _u_values(**{"IfcWallType": 0.25, "IfcSlabType": 0.34, "IfcRoofType": 0.21})
    payload["construction"]["types"] = ["IfcWallType", "IfcSlabType", "IfcRoofType"]

    summary = apply_add_construction(file, payload)

    assert summary["psets_written"] == 3
    expected = {"IfcWallType": ("Pset_WallCommon", 0.25), "IfcSlabType": ("Pset_SlabCommon", 0.34), "IfcRoofType": ("Pset_RoofCommon", 0.21)}
    for cls, (pset_name, u) in expected.items():
        props = ifcopenshell.util.element.get_pset(file.by_type(cls)[0], name=pset_name)
        assert props["ThermalTransmittance"] == pytest.approx(u)


def test_construction_repush_updates_thermal_transmittance():
    file = ifcopenshell.file(schema="IFC4")
    apply_add_construction(file, _u_values(**{"IfcWallType": 0.25}))
    set_before = file.by_type("IfcMaterialLayerSet")[0].id()

    payload2 = _u_values(**{"IfcWallType": 0.31})
    payload2["construction"]["layers"][0]["thickness_m"] = 0.25  # set upsert path stays exercised
    summary = apply_add_construction(file, payload2)

    assert summary["psets_written"] == 1
    assert file.by_type("IfcMaterialLayerSet")[0].id() == set_before  # same upsert semantics
    wall = file.by_type("IfcWallType")[0]
    props = ifcopenshell.util.element.get_pset(wall, name="Pset_WallCommon")
    assert props["ThermalTransmittance"] == pytest.approx(0.31)


def test_construction_without_u_values_writes_no_psets():
    file = ifcopenshell.file(schema="IFC4")

    summary = apply_add_construction(file, _construction_payload())

    assert summary["psets_written"] == 0
    assert ifcopenshell.util.element.get_pset(file.by_type("IfcWallType")[0], name="Pset_WallCommon") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q -k thermal_transmittance`
Expected: FAIL — `assert summary["psets_written"] == 1` → `KeyError: 'psets_written'`.

- [ ] **Step 4: Implement** (`bonsai_addon/insert.py`)

Add near the top (after existing imports):

```python
_THERMAL_TRANSMITTANCE_PSET = {
    "IfcWallType": "Pset_WallCommon",
    "IfcSlabType": "Pset_SlabCommon",
    "IfcRoofType": "Pset_RoofCommon",
}


def _write_thermal_transmittance(file, target, u_value: float) -> bool:
    """Find-or-create the type's Perimeter pset and set ThermalTransmittance."""
    import ifcopenshell.util.element

    pset_name = _THERMAL_TRANSMITTANCE_PSET.get(target.is_a())
    if pset_name is None:
        return False
    found = ifcopenshell.util.element.get_pset(target, name=pset_name)
    if found is None:
        pset = ifcopenshell.api.run("pset.add_pset", file, product=target, name=pset_name)
    else:
        pset = file.by_id(found["id"])
    ifcopenshell.api.run("pset.edit_pset", file, pset=pset, properties={"ThermalTransmittance": u_value})
    return True
```

And the tail of `apply_add_construction` (after the `material.assign_material` call, before `return summary`):

```python
    u_values = construction.get("u_values") or {}
    written = 0
    for target in targets:
        u_value = u_values.get(target.is_a())
        if isinstance(u_value, (int, float)) and not isinstance(u_value, bool):
            if _write_thermal_transmittance(file, target, float(u_value)):
                written += 1
    summary["psets_written"] = written
    return summary
```

If the local `import ifcopenshell.util.element` conflicts with the file's import style, import it at the top-level of `insert.py` instead (both are fine pick whichever matches surrounding imports).

Update the docstring line in `apply_add_construction` to mention: "A `u_values` map (per type class) writes `ThermalTransmittance` into the matching `Pset_<Type>Common` pset, created or edited in place."

- [ ] **Step 5: Status line** (`bonsai_addon/__init__.py`, `_execute` add_construction branch):

```python
            _CLIENT.report(
                "applied",
                f"{result['types_created']} type(s) created, {result['sets_updated']} set(s) updated, "
                f"{result['materials_created']} material(s) created, "
                f"{result['placeholders_matched']} placeholder(s) matched, "
                f"{result['psets_written']} thermal pset(s) written",
            )
```

(Keep existing keys; add the fifth segment.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py tests/test_gui_server.py tests/test_listener_payload.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
ruff check --exclude src/materialsdb/classes.py src tests dev_utils bonsai_addon && ruff format --exclude "src/materialsdb/classes.py" src tests dev_utils bonsai_addon
ty check --project .
git add bonsai_addon/insert.py bonsai_addon/__init__.py tests/test_bonsai_insert.py
git commit -m "feat(bonsai): thermal transmittance psets on pushed constructions"
```

---

### Task 4: composer page — per-row material replace

**Files:**
- Modify: `src/materialsdb/gui/static/app-constructions.js` (`renderLayers` name cell + bindings arena near line 477; new `replaceLayerFromChooser`)
- Modify: `tests/harness/construction_page_harness.mjs` (expose `__replace`, add replace scenario)

**Interfaces:**
- Consumes: existing `openChooser(onPick)`, `attachLayerChoices(layer)`, `refreshU()`, `renderLayers()`.
- Produces: `replaceLayerFromChooser(index)` — returns an async onPick callback that replaces `layers[index]` (thickness preserved, `placeholder` dropped) and re-renders; pencil `title="replace material"` per row; `dblclick` on the name cell opens the chooser with that callback (single listener on the persistent tbody, therefore NOT re-bound per render).

- [ ] **Step 1: harness failing scenario** (append inside `construction_page_harness.mjs` before the trailing `console.log("OK")`, and extend the exposure line `Object.assign(globalThis, {...})` with `__replace: (idx) => replaceLayerFromChooser(idx)`)

```js
console.log("\n== replace: pencil badge present and replace flow converts placeholder ==");
```

- [ ] **Step 2: Implement** — JS changes:

1. In `renderLayers`, wrap the name cell content and append the pencil:

```js
    const replaceBtn = `<span data-role="replace" data-index="${index}" style="cursor:pointer;margin-left:.3rem" title="replace material">✏</span>`;
    const cell = isPlaceholder
      ? `${esc(layer.placeholder.name || "(model material)")} <span style="color:#888;font-size:.8rem">model material</span>`
      : esc(layer.display_name || layer.material_id);
    const nameCell = `${cell}${replaceBtn}`;
```

2. After `bindThicknessControls();` inside `renderLayers`, append the pencil binding (row-specific — rebuilt per render):

```js
  tbody.querySelectorAll("span[data-role=replace]").forEach((span) => {
    span.addEventListener("click", (event) => {
      event.stopPropagation();
      openChooser(replaceLayerFromChooser(Number(span.dataset.index)));
    });
  });
```

3. Once at bootstrap (next to `$("add-layer").onclick = ...`, NOT inside `renderLayers`):

```js
$("layers").addEventListener("dblclick", (event) => {
  const td = event.target.closest("td[data-role=name]");
  if (!td) return;
  const tr = td.closest("tr");
  if (!tr || tr.dataset.index === undefined) return;  // boundary rows have no data-index
  openChooser(replaceLayerFromChooser(Number(tr.dataset.index)));
});
```

(Binding lives on the persistent `<tbody id="layers">`; row `<tr>`s are recreated each render so per-row dblclick would die on the first re-render.)

4. New callback near `addLayerFromChooser`:

```js
function replaceLayerFromChooser(index) {
  return async (picked) => {
    const it = (Array.isArray(picked) ? picked : [picked])[0];
    if (!it || !it.material_id) { setStatus("no material picked"); return; }
    const previous = layers[index];
    const layer = { material_id: it.material_id, thickness_m: previous ? previous.thickness_m : 0.2 };
    layers[index] = layer;
    selectedRow = index;
    renderLayers();
    await attachLayerChoices(layer);
    renderLayers();
    await refreshU();
    setStatus(`replaced layer ${index + 1} material`);
  };
}
```

- [ ] **Step 3: harness scenario** (append to `construction_page_harness.mjs`, before `console.log("OK")`):

```js
console.log("\n== replace flow converts placeholder ==>");
await S.__evalInContext(
  `layers = [{ material_id: null, thickness_m: 0.2, placeholder: { name: "Old", lambda_value: 0.05 } },
             { material_id: "00000000-0000-0000-0000-000000000002", thickness_m: 0.15 }];
   selectedRow = -1; lastResult = null; renderLayers();`);
const pencil = document.getElementById("layers").innerHTML.includes('title="replace material"');
if (!pencil) console.log("BUG REPRODUCED: pencil missing");
await S.__replace(0)("00000000-0000-0000-0000-000000000001");
await sleep(50);
const replaced = S.__layers();
console.log(`replaced material: ${replaced[0]?.material_id === "00000000-0000-0000-0000-000000000001"} | placeholder dropped: ${!replaced[0]?.placeholder} | thickness kept: ${Math.abs(replaced[0]?.thickness_m - 0.2) < 1e-9}`);
if (!pencil || replaced[0]?.material_id !== "00000000-0000-0000-0000-000000000001" || replaced[0]?.placeholder || !Number.isFinite(replaced[0]?.thickness_m) || Math.abs(replaced[0]?.thickness_m - 0.2) > 1e-9) {
  console.log("BUG REPRODUCED: replace flow broken");
  process.exit(1);
}
console.log("replace: OK");
```

- [ ] **Step 4: Verify**

Run: `python3 -m pytest -p no:pytest-blender tests/test_construction_frontend.py -q`
Expected: PASS (harness exits 0 and prints `replace: OK` + existing asserts still green).

- [ ] **Step 5: Commit**

```bash
ruff check --exclude src/materialsdb/classes.py tests && ruff format --exclude "src/materialsdb/classes.py" tests
git add src/materialsdb/gui/static/app-constructions.js tests/harness/construction_page_harness.mjs
git commit -m "feat(gui): replace a construction layer's material via chooser"
```

---

### Task 5: in-Blender harness assert + README docs

**Files:**
- Modify: `bonsai_addon/test/test_in_blender.py` (`test_construction_roundtrip_and_undo`, after the first `send(construction)` + poll)
- Modify: `README.md` (bonsai feature list lines 100-107 region)

**Interfaces:**
- Consumes: Task 2 payload (server-side `u_values` travel through `/api/listener/send` automatically); `ifcopenshell.util.element.get_pset`.

- [ ] **Step 1: harness assert** — after the first `assert wall_types[0].GlobalId` block in `test_construction_roundtrip_and_undo` add (top-of-file import `ifcopenshell.util.element` once):

```python
        # U-value pset written with the wall-direction Rsi/Rse (server-side)
        props = ifcopenshell.util.element.get_pset(wall_types[0], name="Pset_WallCommon")
        assert props is not None and "ThermalTransmittance" in props
```

- [ ] **Step 2: README candles** — under the `# Bonsai integration :` block (README.md ~line 100), extend the sentence about constructions with:

"- constructions push U-values into the per-type `Pset_WallCommon` / `Pset_SlabCommon` / `Pset_RoofCommon` `ThermalTransmittance`" and a second bullet line for the composer: "the construction composer lets you replace any layer's material (pencil or double-click on the material row)".

Final README shape (edit in place, keep surrounding text):

```markdown
the construction page pushes each element type its Pset_*Common.
...
```

(Adapt to the existing prose style; two short sentences, no marketing.)

- [ ] **Step 3: Run the in-Blender harness (local only)**

Run: `python3 -m pytest bonsai_addon/test -q`
Expected: PASS (5 existing tests + the new assert inside the roundtrip test green). If a blade/IFC quirk bites, fix within this task and re-run.

- [ ] **Step 4: Full gates**

Run: `ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon && ruff format --exclude "src/materialsdb/classes.py" --check . && ty check --project . && python3 -m pytest -p no:pytest-blender -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bonsai_addon/test/test_in_blender.py README.md
git commit -m "docs/harness: thermal transmittance round-trip assert + README feature notes"
git push
```

---

## Self-Review

- Spec coverage: Task 1 = spec §1 both helpers; Task 2 = spec §2 payload; Task 3 = spec §3 psets + summary + status; Task 4 = spec §4 GUI replace (pencil + dblclick + placeholder conversion); Task 5 = spec "Testing" in-Blender assert + "Documentation". Upsert-with-new-U covered by test_construction_repush_updates_thermal_transmittance; no-U skip by test_construction_without_u_values_writes_no_psets.
- Placeholders: none (Task 1 Step 1 self-notes an exact replacement to make in the same step, with the final code shown).
- Conflicts: Task 3 Step 1 deliberately changes exact-summary asserts FIRST so the failing test is the missing feature, not a broken baseline.
