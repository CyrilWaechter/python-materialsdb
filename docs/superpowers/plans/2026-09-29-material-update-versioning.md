# Material Update Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect upstream changes to materials already pushed into an IFC model, report the diff, and let the user opt in to update or replace them in place.

**Architecture:** The server computes a per-material fingerprint (`sha256` of the stored XML), caches a model map reported by the add-on (`material_id → fingerprint + layers`), derives status, owns layer matching, and sends the resolved update mapping in the push payload. The add-on writes/reads the fingerprint in the identity pset and applies the mapping mechanically.

**Tech Stack:** Python 3.14, lxml, ifcopenshell, sqlite3, vanilla JS (Node-tested).

**Spec:** `docs/superpowers/specs/2026-09-29-material-update-versioning-design.md`

**Prerequisite:** `docs/superpowers/plans/2026-09-29-construction-material-data-parity.md` must be implemented first (construction materials need `psets` + `org_layer` for matching).

## Global Constraints

- src-layout: `src/materialsdb/` (package), `bonsai_addon/` (Blender extension), `tests/`.
- Local tests: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender -q`.
- Gates: `ruff check --exclude src/materialsdb/classes.py src tests dev_utils bonsai_addon`, `ruff format --check .`, `ty check --project .`.
- `bonsai_addon/insert.py` stays pure ifcopenshell (no `materialsdb` import).
- Fingerprint scheme string: `"materialsdb-fp/1"` (exact).
- Identity pset is named `materialsdb`; new properties: `fingerprint`, `fingerprint_scheme` (IfcText).
- Never auto-update: an existing material is only changed when its entry's `mode` is `update` or `replace`.

## Review Focus

- A model material with only an identity pset and no `org_layer` (legacy) must be `unknown`, never `changed`.
- A push whose update mapping references a model layer that is not in the model (edited by the user since the map) must report and skip that layer, not crash.
- `mode="skip"` on a changed material must leave the model untouched and appear in the safety-net report (no fingerprint backfill that would mask it).
- Two same-thickness candidates must not be auto-mapped; the server marks the material ambiguous.
- An offline add-on must leave the last model map served with `seen_at`, and actions must not fire.

---

### Task 1: Store fingerprint + model map + push snapshots

**Files:**
- Modify: `src/materialsdb/store.py` (`_SCHEMA`, new methods, module constant)
- Test: `tests/test_store.py`

**Interfaces:**
- Produces:
  - `FINGERPRINT_SCHEME = "materialsdb-fp/1"` (module constant)
  - `MaterialStore.material_fingerprint(material_id: str) -> str | None` — hex `sha256` of the stored `xml` blob, `None` if the material is absent.
  - `MaterialStore.set_model_materials(model_path: str, materials: dict[str, dict]) -> None` — each value `{"fingerprint": str | None, "scheme": str | None, "layers": [{"layer_id": str, "thick": float | None}]}`; replaces all rows for `model_path`.
  - `MaterialStore.get_model_materials(model_path: str) -> dict[str, dict]` — inverse shape, with `seen_at` per row excluded (a parallel `model_seen_at(model_path) -> float | None`).
  - `MaterialStore.record_pushed_materials(model_path: str, entries: list[dict]) -> None` — upsert per `(model_path, identity.material_id, layer_id or "")` with `snapshot = json.dumps(entry)`.
  - `MaterialStore.get_pushed_material(model_path: str, material_id: str, layer_id: str | None = None) -> dict | None`.
- New tables appended to `_SCHEMA` (created by the existing `executescript`, no version bump):
  `model_materials(model_path, material_id, fingerprint, scheme, layers, seen_at, PRIMARY KEY(model_path, material_id))`
  `pushed_materials(model_path, material_id, layer_id, snapshot, pushed_at, PRIMARY KEY(model_path, material_id, layer_id))`

- [ ] **Step 1: Write the failing tests** in `tests/test_store.py`

```python
def test_material_fingerprint_is_stable_and_changes(mini_xml, tmp_path):
    store = MaterialStore(db_path=tmp_path / "fp.db")
    store.refresh(paths=[mini_xml])
    mid = "00000000-0000-0000-0000-000000000001"
    first = store.material_fingerprint(mid)
    assert first and len(first) == 64
    store.refresh(paths=[mini_xml])            # unchanged file
    assert store.material_fingerprint(mid) == first
    assert store.material_fingerprint("nope") is None


def test_model_materials_roundtrip(tmp_path):
    store = MaterialStore(db_path=tmp_path / "m.db")
    store.set_model_materials("/m/a.ifc", {"ID1": {"fingerprint": "f", "scheme": "s", "layers": [{"layer_id": "L1", "thick": 0.2}]}})
    got = store.get_model_materials("/m/a.ifc")
    assert got == {"ID1": {"fingerprint": "f", "scheme": "s", "layers": [{"layer_id": "L1", "thick": 0.2}]}}
    assert store.model_seen_at("/m/a.ifc") is not None
    store.set_model_materials("/m/a.ifc", {})   # replace-all
    assert store.get_model_materials("/m/a.ifc") == {}


def test_pushed_materials_roundtrip(tmp_path):
    store = MaterialStore(db_path=tmp_path / "p.db")
    entry = {"identity": {"material_id": "ID1"}, "layer_id": "L1", "name": "N"}
    store.record_pushed_materials("/m/a.ifc", [entry])
    assert store.get_pushed_material("/m/a.ifc", "ID1", "L1") == entry
    assert store.get_pushed_material("/m/a.ifc", "ID1", "L2") is None
```

- [ ] **Step 2: Run to verify failure** — `... tests/test_store.py -k "fingerprint_is_stable or roundtrip" -v` → FAIL (AttributeError).
- [ ] **Step 3: Implement** the constant, the two tables in `_SCHEMA`, and the methods (sqlite3, `INSERT … ON CONFLICT DO UPDATE`).
- [ ] **Step 4: Run** `... tests/test_store.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(store): material fingerprint + model map + push snapshots`.

---

### Task 2: Write/read the fingerprint in the identity pset

**Files:**
- Modify: `bonsai_addon/insert.py` (`_add_identity_pset`, and the `_should_style` vicinity is untouched)
- Modify: `src/materialsdb/ifc/material_builder.py` (`_create_identity_pset` optional args, `build` passthrough)
- Test: `tests/test_bonsai_insert.py`

**Interfaces:**
- `_add_identity_pset(file, material, identity)` also writes `fingerprint` and `fingerprint_scheme` (IfcText) when present in `identity`.
- `MaterialBuilder._create_identity_pset(ifc_material, material, company_id, company, verxml, fingerprint=None, scheme=None)` writes them when not `None`; `build(..., fingerprint=None, scheme=None)` threads them.

- [ ] **Step 1: Failing test**

```python
def test_identity_pset_writes_fingerprint():
    file = ifcopenshell.file(schema="IFC4")
    material = ifcopenshell.api.run("material.add_material", file, name="M")
    insert._add_identity_pset(file, material, {"material_id": "X", "company_id": "C", "company": "Co",
                                               "fingerprint": "abc", "fingerprint_scheme": "materialsdb-fp/1"})
    pset = next(p for p in file.by_type("IfcMaterialProperties") if p.Name == "materialsdb")
    props = {p.Name: p.NominalValue.wrappedValue for p in pset.Properties}
    assert props["fingerprint"] == "abc" and props["fingerprint_scheme"] == "materialsdb-fp/1"
```

Add a `test_material_builder_identity_omits_fingerprint_when_absent` mirroring the MaterialBuilder path.

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `tests/test_bonsai_insert.py tests/test_material_builder.py -q` → PASS.**
- [ ] **Step 5: Commit** `feat(ifc): fingerprint properties in the materialsdb identity pset`.

---

### Task 3: Payloads carry fingerprint, mode, replaces and the update mapping

**Files:**
- Modify: `src/materialsdb/gui/listener.py` (`build_add_materials_payload`, `build_add_construction_payload`)
- Test: `tests/test_listener_payload.py`

**Interfaces:**
- `item`/layer accepts `mode ∈ {skip, update, replace}`, `replaces: {"material_id": str, "layer_id": str | None} | None`, `update: dict[str, str] | None`.
- Every emitted material entry gains `"mode"`, `"replaces"`, `"update"`; `identity` gains `"fingerprint"` and `"fingerprint_scheme"` from `store_.material_fingerprint(id)` + `FINGERPRINT_SCHEME`.
- Defaults: `mode="skip"`, `replaces=None`, `update=None`.

- [ ] **Step 1: Failing tests**

```python
def test_payload_carries_fingerprint_and_default_skip(store):
    payload, _ = build_add_materials_payload(store, [{"id": "00000000-0000-0000-0000-000000000001"}])
    first = payload["materials"][0]
    assert first["mode"] == "skip" and first["replaces"] is None and first["update"] is None
    assert first["identity"]["fingerprint_scheme"] == "materialsdb-fp/1"
    assert first["identity"]["fingerprint"]

def test_payload_carries_update_mapping(store):
    payload, _ = build_add_materials_payload(store, [{
        "id": "00000000-0000-0000-0000-000000000001", "mode": "update",
        "update": {"00000000-0000-0000-0000-0000000000a1": "00000000-0000-0000-0000-0000000000a1"}}])
    assert payload["materials"][0]["mode"] == "update"
    assert payload["materials"][0]["update"]
```

(Mirror one construction-payload assertion.)

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** (import `FINGERPRINT_SCHEME`; pass the three fields through both builders).
- [ ] **Step 4: Run `tests/test_listener_payload.py -q` → PASS.**
- [ ] **Step 5: Commit** `feat(listener): push fingerprint, mode, replaces and update mapping`.

---

### Task 4: Server ingests the model map and records push snapshots

**Files:**
- Modify: `src/materialsdb/gui/server.py` (`do_POST` dispatch, new `_listener_model`, `_listener_send` snapshot hook)
- Test: `tests/test_gui_server.py`

**Interfaces:**
- `POST /api/listener/model` (token-gated) body `{client_id, model_path, scheme, materials}` → `state.resolve_store().set_model_materials(model_path, materials)`; `200 {"ok": True}`.
- After building a push payload in `_listener_send`, call `store.record_pushed_materials(model_path, entries)` where entries are `payload["materials"]` or `payload["construction"]["layers"][*]["material"]`.

- [ ] **Step 1: Failing test** (extend the existing `api` fixture):

```python
def test_listener_model_map_ingested(api, tmp_path):
    payload = {"client_id": "c1", "model_path": str(tmp_path / "m.ifc"), "scheme": "materialsdb-fp/1",
               "materials": {"ID1": {"fingerprint": "f", "scheme": "materialsdb-fp/1", "layers": []}}}
    status, _ = server_call(api, "POST", "/api/listener/model", payload=payload, token=api.state.token)
    assert status == 200
    assert api.state.resolve_store().get_model_materials(str(tmp_path / "m.ifc"))["ID1"]["fingerprint"] == "f"

def test_listener_send_records_snapshot(api, tmp_path):
    # register a listener for model_path, POST /api/listener/send, then assert
    # store.get_pushed_material(model_path, material_id, layer_id) is not None
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the endpoint + snapshot hook.
- [ ] **Step 4: Run `tests/test_gui_server.py -q` → PASS.**
- [ ] **Step 5: Commit** `feat(gui): ingest model material map; record push snapshots`.

---

### Task 5: Status, matching and `/api/model/changes`

**Files:**
- Modify: `src/materialsdb/gui/server.py`
- Test: `tests/test_gui_server.py`

**Interfaces:**
- `_active_model_path() -> str | None` — `model_path` of the most recent listener.
- `_material_status(store, model_path, material_id) -> str` in `{"absent","current","changed","unknown","gone"}`.
- `_match_layers(store, material_id, model_layers) -> dict` → `{"state": "updatable"|"ambiguous"|"unmatched"|"unknown", "mapping": {model_layer_id: new_layer_id}, "candidates": {model_layer_id: [new_layer_id, ...]}}` implementing: id match → thickness match → thick-less requires exactly one new layer. A `btk`/`complex` material (store summary `type != "simple"`) is always `unmatched` (not updatable in place).
- `GET /api/model/changes` → `{"model_path", "seen_at", "counts": {"changed","gone","unknown"}, "changed": [{"material_id", "matching", "report": [...], "has_snapshot": bool}], "gone": [...], "unknown": [...]}` where `report` is a list of `{"field", "old", "new"}` built from `get_pushed_material` vs the current store values (empty + `has_snapshot=False` when absent).
- `/api/materials` rows gain `"model": <status>`.

- [ ] **Step 1: Failing tests** — map `ID1` with `fingerprint "old"` while the store's digest differs → `changed`; same digest → `current`; unknown id → `gone`; no fingerprint → `unknown`; **a model entry with a fingerprint but no `layers` (no `org_layer`) still resolves `unknown`/`unmatched`, never `changed`**; a different `scheme` → `unknown`; two same-thickness new layers → `ambiguous`; a `btk` store material → `unmatched`; `/api/model/changes` counts and, with no listener connected, the response carries the cached `seen_at` and empty actions.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the status/matching/report helpers and wire the endpoint + listing field.
- [ ] **Step 4: Run `tests/test_gui_server.py -q` → PASS.**
- [ ] **Step 5: Commit** `feat(gui): model change status, layer matching and /api/model/changes`.

---

### Task 6: Add-on reports the model map

**Files:**
- Modify: `bonsai_addon/discovery.py` (`ListenerClient.send_model_map`)
- Modify: `bonsai_addon/__init__.py` (build map; send on register; send after pushes)
- Test: `tests/test_bonsai_discovery.py`

**Interfaces:**
- `ListenerClient.send_model_map(model_path: str, materials: dict) -> None` → `_request("POST", "/api/listener/model", {"client_id":…, "model_path":…, "scheme": FINGERPRINT_SCHEME, "materials":…})`.
- `_model_material_map(file) -> dict[str, dict]` — scan `IfcMaterial`, read the `materialsdb` pset's `fingerprint`/`fingerprint_scheme`/`materialsdb.org_layer.layer_id`/`thick`, dedupe per `material_id`, layers list.
- Called after `register` and after every successful `add_materials`/`add_construction` apply.

- [ ] **Step 1: Failing test** for `send_model_map` using a stub `_request` recorder, plus a pure test of the map builder on a scratch IFC built with `_add_identity_pset`.
- [ ] **Step 2–4: Red → implement → green** (`tests/test_bonsai_discovery.py -q`).
- [ ] **Step 5: Commit** `feat(bonsai): report the model material map to the GUI`.

---

### Task 7: Add-on applies an update mapping

**Files:**
- Modify: `bonsai_addon/insert.py` (`_model_layers`, `_apply_update`, and `mode` handling in both apply functions)
- Test: `tests/test_bonsai_insert.py`

**Interfaces:**
- `_model_layers(file, material_id) -> dict[str, list[tuple[entity, entity | None]]]` — model layer id (`materialsdb.org_layer.layer_id`) → `[(IfcMaterial, IfcMaterialLayer | None)]`.
- `_apply_update(file, entry, material, layers_by_id, summary) -> None` — for each `(model_layer_id, new_layer_id)` in `entry["update"]`: locate the model layer, refresh identity pset, name/description/category, `_add_property_psets` (replacing our psets), layer thickness/name/description and `org_layer`; refresh style. Missing model layer → append to `summary["update_missing"]`.
- `apply_add_materials` / `apply_add_construction`: when the existing material's `fingerprint` differs from `entry["identity"]["fingerprint"]` and `entry["mode"] == "update"`, call `_apply_update`; when `mode == "skip"` and it differs, append to `summary["changed_skipped"]`; when the material has **no** `fingerprint` (legacy baseline), write `entry["identity"]["fingerprint"]`/`fingerprint_scheme` onto it (bookkeeping only, no warning) regardless of mode.

- [ ] **Step 1: Failing tests** — build a model with `_add_identity_pset("...fingerprint":"old")` and a payload entry `mode="update"` with `fingerprint "new"` and an `update` mapping; assert the GUID is unchanged, the pset is refreshed (foreign pset kept), `fingerprint` updated; a mapping to a missing model layer lands in `update_missing`; `mode="skip"` leaves data and lists `changed_skipped`; a legacy material (no fingerprint) gets the baseline written with no `changed_skipped`.
- [ ] **Step 2–4: Red → implement → green** (`tests/test_bonsai_insert.py -q`).
- [ ] **Step 5: Commit** `feat(bonsai): opt-in in-place material update`.

---

### Task 8: Add-on applies a replacement and the safety net

**Files:**
- Modify: `bonsai_addon/insert.py` (`_apply_replace`, summary wiring, `_execute` reporting in `__init__.py`)
- Test: `tests/test_bonsai_insert.py`

**Interfaces:**
- `_apply_replace(file, entry, material, summary) -> None` — remove the superseded `IfcMaterial`/layer/psets/style (`material_builder.purge_material` equivalent lives in the add-on: keep it pure and local), then create the replacement entry's material and re-attach it to the same `IfcMaterialLayer`/set position. Layer-level when `entry["replaces"]["layer_id"]` is set, whole-material otherwise (all model layers for the material).
- `__init__.py` push report appends `"; N changed material(s) left unchanged"` when `changed_skipped` is non-empty.

- [ ] **Step 1: Failing tests** — replace one layer of a 2-layer set: superseded `IfcMaterial` gone, set has the replacement material at the same position/thickness; whole-material replace removes all old layers.
- [ ] **Step 2–4: Red → implement → green.**
- [ ] **Step 5: Commit** `feat(bonsai): opt-in material replacement + changed-skip report`.

---

### Task 9: Picker UI

**Files:**
- Modify: `src/materialsdb/gui/static/index.html` (banner `#model-changes`, report/action containers)
- Modify: `src/materialsdb/gui/static/app.js` (fetch `/api/model/changes`, row status, action handlers, send `mode`/`update`/`replaces`)
- Test: `tests/harness/model_changes_harness.mjs` + `tests/test_model_changes_frontend.py`

**Interfaces (JS):**
- `renderModelChanges(data)` fills the banner with counts and sets `data-model-status` on material rows.
- `openChangeReport(materialId)` renders the `report` rows (old → new) plus the actions.
- `chooseLayerMapping(materialId)` renders candidate `<select>` per ambiguous model layer.
- `sendSelected()` includes for each selected item `mode` (`skip`/`update`/`replace`), `update` (the chosen mapping) and `replaces` when replacing.

- [ ] **Step 1: Node harness test** (DOM stubs, like `construction_page_harness.mjs`) asserting a changed material renders the banner count, the report rows, and that choosing "update" puts `mode:"update"` + `update` in the send payload.
- [ ] **Step 2: Run `PYTHONPATH=src python3 -m pytest -p no:pytest-blender tests/test_model_changes_frontend.py -q` → FAIL.**
- [ ] **Step 3: Implement** the JS/HTML.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(gui): picker model-change status, report and update/replace actions`.

---

### Task 10: In-Blender harness + full gate

**Files:**
- Modify: `bonsai_addon/test/test_in_blender.py`
- Test: full suite

- [ ] **Step 1: Add an in-Blender flow** — push a material; edit its stored XML; re-push with `mode="update"`; assert the `IfcMaterial` GUID is unchanged and a pset value changed; assert the model map was re-sent (a second `POST /api/listener/model` observed).

- [ ] **Step 2: Run the local in-Blender harness** `pytest bonsai_addon/test/test_in_blender.py -q` (local-only; Blender required).

- [ ] **Step 3: Full gates**

```bash
ruff check --exclude src/materialsdb/classes.py src tests dev_utils bonsai_addon
ruff format --check .
ty check --project .
PYTHONPATH=src python3 -m pytest -p no:pytest-blender -q
```
Expected: all pass.

- [ ] **Step 4: Commit** `test(bonsai): in-Blender material update flow`.
