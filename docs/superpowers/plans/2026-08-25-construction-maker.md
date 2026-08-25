# Construction Maker v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compose thermal constructions (ordered material+thickness layers) with standards-based U-values, persisted as JSON files, emitted as IfcMaterialLayerSet, and edited through a new GUI section.

**Architecture:** New `src/materialsdb/construction.py` owns the stack model, resistance-preset registry and U-math plus IFC emission; `gui/server.py` gains construction CRUD + `/api/u_value`; the frontend gets a second page (`constructions.html`) linked from the picker, sharing token/session machinery.

**Tech Stack:** Unchanged — stdlib server, vanilla JS, dataclasses, sqlite store, ifcopenshell `[ifc]`, pytest/http.client.

## Global Constraints

- Zero new runtime dependencies; no store-schema change (constructions are JSON files).
- Layer order convention: index 0 = EXTERIOR side. Canonical thickness unit: meters in model/API; millimetres in UI only.
- Resistance presets ship as verified data:
  ISO6946 = {"wall": (0.13, 0.04), "roof": (0.10, 0.04), "floor": (0.17, 0.04), "generic": (0.13, 0.04)}
  SIA180  = identical numbers to ISO6946 for these boundary cases (SIA 180 references the same ISO 6946 table; confirmed via multiple secondary sources). Kept as a separate selectable entry so future SIA-specific corrections have a home. Code comments must state this equivalence + source.
- Lambda resolution for a referenced material: FIRST country-resolved lambda_value found across its layers (get_by_country per layer, first non-None wins); layers of a construction whose material lacks lambda are excluded from the sum and reported in UResult.missing_lambda_ids.
- designUsage maps to flow direction: consDesignForWall->"wall", consDesignForRoof->"roof", consDesignForFloor->"floor"; None -> "generic".
- Save-time validation: every material id must resolve in the store; thickness must be a positive finite number. Errors name the offenders.
- Slug rule: lowercase, non-alphanumeric runs -> "-", trimmed; collisions get "-2", "-3"... suffixes.
- Tooling gates green after every task: `python3 -m pytest -p no:pytest-blender -q`; ruff check/format and ty per AGENTS.md (untracked scratch excluded locally).
- NEVER `git add tests/` wholesale (untracked symlink hazard); stage explicit paths.
- Frontend JS untested by automation; `node --check` mandatory whenever js changes.
- Commit style: short lowercase imperative.

## Key current signatures (orientation)

```python
# gui/server.py
GuiState(store=None); .token; .file/.session_path; _authorized(); _send(...); _read_json()
GET routes pattern in do_GET; POST dispatch in do_POST with _authorized() first
static serving: STATIC_DIR/index.html + app.js with FileNotFoundError->404 guard
# material_builder.py
MaterialBuilder.build(self, material, company_id="", company="", verxml=None, layer_ids=None) -> list
add_material(file, material, company_id="", company="", verxml=None, replace=False, layer_ids=None)
ProjectLibrary(schema="IFC4"); .create_application(); .create_project_library(company, companyid, ver, crd)
utils.new_tdatetime(); utils.get_by_country(values, country); config.get_lang/get_country
store.get(id) -> Material | None ; store.summaries(...) rows carry display_name (Task: picker v1.1)
# fixtures: mini_producer.xml (Isolant A: 2 layers CH lambda .036/.05 thick 200/100mm),
#           mixed_types.xml (btk + construction examples)
```

---

### Task 1: Core model, presets and U-value math

**Files:**
- Create: `src/materialsdb/construction.py`
- Create: `tests/test_construction.py`

**Interfaces:**
- Consumes: store.get(id), utils.get_by_country, config.get_country
- Produces (used by Tasks 2-4):

```python
@dataclass
class ConstructionLayer:
    material_id: str
    thickness_m: float

@dataclass
class Construction:
    name: str
    design_usage: str | None          # consDesignForWall | consDesignForRoof | consDesignForFloor | None
    layers: list[ConstructionLayer]   # [0] = exterior

RESISTANCE_PRESETS: dict[str, dict[str, tuple[float, float]]]   # ISO6946, SIA180

@dataclass
class UResult:
    u: float | None
    rsi: float
    rse: float
    contributions: list[dict]    # {material_id, name, d_m, lambda_value, r}
    missing_lambda_ids: list[str]

def u_value(construction: Construction, store_, preset: str = "ISO6946") -> UResult
def resolve_lambda(store_, material_id, country=None) -> float | None
```

- [ ] **Step 1: Write failing tests**

Create `tests/test_construction.py`:

```python
import math

import pytest

from materialsdb.construction import (
    RESISTANCE_PRESETS,
    Construction,
    ConstructionLayer,
    u_value,
)


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


@pytest.fixture
def store(tmp_path, mini_xml):
    from materialsdb.store import MaterialStore

    s = MaterialStore(db_path=tmp_path / "c.db")
    s.refresh(paths=[mini_xml])
    yield s
    s.close()


def make_construction(design_usage="consDesignForWall"):
    return Construction(
        name="Test wall",
        design_usage=design_usage,
        layers=[
            ConstructionLayer("00000000-0000-0000-0000-000000000002", thickness_m=0.15),  # Beton B lambda .21
            ConstructionLayer("00000000-0000-0000-0000-000000000001", thickness_m=0.2),   # Isolant A CH lambda .036
        ],
    )


def test_u_value_known_answer_wall_iso6946(store):
    result = u_value(make_construction(), store, preset="ISO6946")

    # R = .13 + (.15/.21 + .2/.036) + .04 = .17 + .714285... + 5.5555...
    expected_r = 0.13 + 0.15 / 0.21 + 0.2 / 0.036 + 0.04
    assert result.u == pytest.approx(1 / expected_r)
    assert [c["d_m"] for c in result.contributions] == [0.15, 0.2]
    assert [c["lambda_value"] for c in result.contributions] == [0.21, 0.036]
    assert result.missing_lambda_ids == []


def test_design_usage_selects_rsi(store):
    roof = u_value(make_construction("consDesignForRoof"), store)
    floor = u_value(make_construction("consDesignForFloor"), store)
    wall = u_value(make_construction("consDesignForWall"), store)

    assert roof.rsi == 0.10 and floor.rsi == 0.17 and wall.rsi == 0.13
    assert roof.u > wall.u > floor.u  # smaller Rsi -> larger U


def test_missing_lambda_layers_flagged_and_excluded(store, mixed_xml):
    store.refresh(paths=[mixed_xml])
    construction = Construction(
        name="with btk",
        design_usage=None,
        layers=[
            ConstructionLayer("00000000-0000-0000-0000-000000000004", thickness_m=0.3),  # btk: no lambda
            ConstructionLayer("00000000-0000-0000-0000-000000000002", thickness_m=0.1),
        ],
    )

    result = u_value(construction, store)

    assert result.missing_lambda_ids == ["00000000-0000-0000-0000-000000000004"]
    assert [c["material_id"][-3:] for c in result.contributions] == ["002"]


def test_unknown_preset_raises(store):
    with pytest.raises(ValueError, match="unknown preset"):
        u_value(make_construction(), store, preset="NOPE")


def test_presets_carry_verified_numbers():
    assert RESISTANCE_PRESETS["ISO6946"]["wall"] == (0.13, 0.04)
    assert RESISTANCE_PRESETS["ISO6946"]["roof"] == (0.10, 0.04)
    assert RESISTANCE_PRESETS["ISO6946"]["floor"] == (0.17, 0.04)
    assert RESISTANCE_PRESETS["SIA180"]["wall"] == (0.13, 0.04)
```

Run: `python3 -m pytest -p no:pytest-blender tests/test_construction.py -v`
Expected: FAIL (`No module named 'materialsdb.construction'`).

- [ ] **Step 2: Implement construction.py**

Create `src/materialsdb/construction.py`:

```python
"""Thermal construction composition: stack model, U-value math, IFC emission."""
import math
from dataclasses import dataclass, field

from materialsdb import config, utils

# Surface resistances (m2K/W): interior/exterior by heat-flow direction.
# ISO 6946 table values; SIA 180 references the same table for these boundary
# cases (verified against secondary literature 2026-08). Kept as separate
# entries so standard-specific corrections have an explicit home.
RESISTANCE_PRESETS = {
    "ISO6946": {
        "wall": (0.13, 0.04),
        "roof": (0.10, 0.04),
        "floor": (0.17, 0.04),
        "generic": (0.13, 0.04),
    },
    "SIA180": {
        "wall": (0.13, 0.04),
        "roof": (0.10, 0.04),
        "floor": (0.17, 0.04),
        "generic": (0.13, 0.04),
    },
}

_DESIGN_USAGE_TO_DIRECTION = {
    "consDesignForWall": "wall",
    "consDesignForRoof": "roof",
    "consDesignForFloor": "floor",
}


@dataclass
class ConstructionLayer:
    material_id: str
    thickness_m: float


@dataclass
class Construction:
    name: str
    design_usage: str | None
    layers: list[ConstructionLayer] = field(default_factory=list)


@dataclass
class UResult:
    u: float | None
    rsi: float
    rse: float
    contributions: list[dict]
    missing_lambda_ids: list[str]


def resolve_lambda(store_, material_id: str, country: str | None = None) -> float | None:
    """First country-resolved lambda_value across the material's layers."""
    material = store_.get(material_id)
    if material is None:
        return None
    country = country or config.get_country()
    for layer in getattr(getattr(material, "layers", None), "layer", ()) or ():
        thermal = utils.get_by_country(layer.thermal or (), country)
        if thermal is not None and thermal.lambda_value is not None:
            return thermal.lambda_value
    return None


def _direction(design_usage: str | None) -> str:
    return _DESIGN_USAGE_TO_DIRECTION.get(design_usage or "", "generic")


def u_value(construction: Construction, store_, preset: str = "ISO6946") -> UResult:
    if preset not in RESISTANCE_PRESETS:
        raise ValueError(f"unknown preset: {preset} (available: {sorted(RESISTANCE_PRESETS)})")
    direction = _direction(construction.design_usage)
    rsi, rse = RESISTANCE_PRESETS[preset][direction]
    country = config.get_country()

    contributions = []
    missing = []
    r_sum = 0.0
    for layer in construction.layers:
        name = ""
        summary = store_.get_summary(layer.material_id)
        if summary is not None:
            name = summary.names.get(config.get_lang()) or summary.names.get("") or ""
        lambda_value = resolve_lambda(store_, layer.material_id, country)
        if lambda_value is None or not layer.thickness_m:
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
            }
        )

    if missing or not contributions or math.isclose(r_sum, 0):
        return UResult(u=None, rsi=rsi, rse=rse, contributions=contributions, missing_lambda_ids=missing)

    u = 1 / (rsi + r_sum + rse)
    return UResult(u=u, rsi=rsi, rse=rse, contributions=contributions, missing_lambda_ids=missing)
```

Design note encoded above: when ANY layer lacks lambda the U value is None (a wrong number is worse than no number) while contributions still show what resolved; the flagged ids explain why.

- [ ] **Step 3: Verify + commit**

Run Step 1 command: all PASS. Full suite green. Gates green.

```bash
git add src/materialsdb/construction.py tests/test_construction.py
git commit -m "feat: construction stack model with preset-based u-value math"
```

---

### Task 2: IFC emission (to_ifc_layer_set)

**Files:**
- Modify: `src/materialsdb/construction.py` (append)
- Modify: `tests/test_construction.py`

**Interfaces:**
- Consumes: Task 1 model; `MaterialBuilder.build(material, ..., layer_ids=[first_layer_guid])` (single representative variant -> exactly one IfcMaterial per referenced material, identity pset included); `ProjectLibrary.create_project_library(company, companyid, ver, crd)`
- Produces:

```python
def to_ifc_layer_set(construction, store_, file=None) -> ifcopenshell.file
    # file=None -> fresh wrapper library (company "MaterialsDB Constructions")
    # creates one IfcMaterial per construction layer via builder.build(layer_ids=[first guid])
    # then IfcMaterialLayer(Material=..., LayerThickness=<construction thickness_m>, Name="<name> | <mm> mm")
    # and IfcMaterialLayerSet(MaterialLayers=[...], LayerSetName=construction.name)
    # raises ValueError listing unknown material ids before touching the file
```

- [ ] **Step 1: Write failing test**

Append to `tests/test_construction.py`:

```python
pytest.importorskip("ifcopenshell")


def test_to_ifc_layer_set_roundtrip(store, tmp_path):
    import ifcopenshell

    from materialsdb.construction import ConstructionLayer, to_ifc_layer_set

    construction = make_construction()
    file = to_ifc_layer_set(construction, store)

    out = tmp_path / "construction.ifc"
    file.write(str(out))
    reopened = ifcopenshell.open(str(out))

    layers = sorted(reopened.by_type("IfcMaterialLayer"), key=lambda l: l.LayerThickness)
    assert [round(l.LayerThickness, 3) for l in layers] == [0.15, 0.2]
    assert {l.Description for l in layers} == {"00000000-0000-0000-0000-000000000002",
                                               "00000000-0000-0000-0000-000000000001"}
    layer_sets = reopened.by_type("IfcMaterialLayerSet")
    assert len(layer_sets) == 1 and layer_sets[0].Name == "Test wall"
    # identity psets ride along on referenced materials
    identity = [p for p in reopened.by_type("IfcMaterialProperties") if p.Name == "materialsdb"]
    assert len(identity) == 2


def test_to_ifc_rejects_unknown_materials(store):
    from materialsdb.construction import ConstructionLayer, to_ifc_layer_set

    bad = Construction(
        name="bad", design_usage=None,
        layers=[ConstructionLayer("ffffffff-0000-0000-0000-000000000000", thickness_m=0.1)],
    )
    with pytest.raises(ValueError, match="ffffffff"):
        to_ifc_layer_set(bad, store)
```

NOTE: `Description` on each created IfcMaterialLayer must carry the SOURCE material guid of the construction layer (distinct from the existing builder behaviour which uses source LAYER guids) - implement accordingly.

Run: FAIL (`ImportError: to_ifc_layer_set`).

- [ ] **Step 2: Implement**

Append to `src/materialsdb/construction.py`:

```python
def to_ifc_layer_set(construction: Construction, store_, file=None):
    """Emit a wrapper IFC library containing the construction as an
    IfcMaterialLayerSet. Referenced materials are built through
    MaterialBuilder (single representative variant) so their identity psets
    ride along; IfcMaterialLayer.Description carries the source material guid."""
    import uuid

    import ifcopenshell

    from materialsdb import utils as mdb_utils
    from materialsdb.ifc.material_builder import MaterialBuilder
    from materialsdb.ifc.project_library import ProjectLibrary

    missing = [
        layer.material_id
        for layer in construction.layers
        if store_.get(layer.material_id) is None
    ]
    if missing:
        raise ValueError(f"unknown material ids: {', '.join(missing)}")

    if file is None:
        library = ProjectLibrary()
        library.create_project_library(
            company="MaterialsDB Constructions",
            companyid=str(uuid.uuid4()),
            ver=1,
            crd=mdb_utils.new_tdatetime(),
        )
        target_file = library.file
    else:
        library = None
        target_file = file

    builder = MaterialBuilder(target_file)
    material_layers = []
    for layer in construction.layers:
        material = store_.get(layer.material_id)
        first_guid = str(material.layers.layer[0].id)
        created = builder.build(material, company_id=str(store_.get_summary(layer.material_id).company_id),
                                company=store_.get_summary(layer.material_id).company,
                                layer_ids={first_guid})
        assert len(created) == 1, "layer_ids subset must yield exactly one IfcMaterial"
        name = store_.get_summary(layer.material_id).names.get(config.get_lang()) or ""
        element_name = f"{name} | {round(layer.thickness_m * 1000)} mm"
        ifc_layer = target_file.create_entity(
            "IfcMaterialLayer",
            Material=created[0],
            LayerThickness=layer.thickness_m,
            Description=layer.material_id,
            Name=element_name,
        )
        material_layers.append(ifc_layer)

    target_file.create_entity(
        "IfcMaterialLayerSet",
        MaterialLayers=material_layers,
        LayerSetName=construction.name,
    )
    return target_file if library is None else library.file
```

Cleanup note while transcribing: the double `store_.get_summary(...)` call per layer is wasteful - fetch once into `summary = store_.get_summary(layer.material_id)` and reuse for company_id/company/name.

- [ ] **Step 3: Verify + commit**

All construction tests + full suite green; gates green.

```bash
git add src/materialsdb/construction.py tests/test_construction.py
git commit -m "feat: emit constructions as ifc material layer sets"
```

---

### Task 3: Persistence library + server endpoints

**Files:**
- Modify: `src/materialsdb/construction.py` (append persistence helpers)
- Modify: `src/materialsdb/gui/server.py`
- Modify: `tests/test_construction.py`, `tests/test_gui_server.py`

**Interfaces:**
- Consumes: Task 1/2; `cache.get_cache_folder()`; GuiState
- Produces:

```python
# construction.py
def constructions_dir() -> Path                      # <cache>/constructions, mkdir parents
def slugify(name: str) -> str                        # lowercase, non-alnum runs -> "-", strip "-"
def save_construction(construction, store_) -> Path  # validates ids+thickness, writes slug(name).json
def load_construction(name_or_slug, store_) -> Construction   # FileNotFoundError-safe KeyError? -> returns None if absent
def list_constructions() -> list[str]                # names from *.json
def delete_construction(name_or_slug) -> bool

# gui routes (token gate on mutating ones):
GET    /api/constructions                 -> {"constructions": ["name", ...]}
GET    /api/constructions/{name}          -> json body | 404
POST   /api/constructions/{name}          -> validate+save | 400 offenders
DELETE /api/constructions/{name}          -> {"deleted": true} | 404
POST   /api/u_value                       -> UResult dict | 400 validation errors
```

Validation helper shared by save + u_value:

```python
def validate_construction(body: dict, store_) -> tuple[Construction, list[str]]
    # returns (construction, problems); problems list human-readable strings
```

- [ ] **Step 1: Write failing tests**

Append to `tests/test_construction.py` (persistence, no server):

```python
def test_save_load_list_delete_roundtrip(store, tmp_path, monkeypatch):
    import materialsdb.construction as cm

    monkeypatch.setattr(cm, "constructions_dir", lambda: tmp_path / "constr")
    construction = make_construction()

    path = cm.save_construction(construction, store)
    assert path.exists() and path.name == "test-wall.json"
    assert cm.list_constructions() == ["Test wall"]

    loaded = cm.load_construction("Test wall", store)
    assert loaded == construction

    assert cm.delete_construction("Test wall") is True
    assert cm.delete_construction("Test wall") is False


def test_slug_collision_suffixes(store, tmp_path, monkeypatch):
    import materialsdb.construction as cm

    monkeypatch.setattr(cm, "constructions_dir", lambda: tmp_path / "constr")
    cm.save_construction(make_construction(), store)
    second = make_construction()
    second.name = "Test wall!"
    path2 = cm.save_construction(second, store)
    assert path2.name == "test-wall-2.json"


def test_save_rejects_unknown_material_and_bad_thickness(store, tmp_path, monkeypatch):
    import materialsdb.construction as cm

    monkeypatch.setattr(cm, "constructions_dir", lambda: tmp_path / "constr")
    bad_ids = make_construction()
    bad_ids.layers[0].material_id = "ffffffff-0000-0000-0000-000000000000"
    construction, problems = cm.validate_construction(
        {"name": "x", "design_usage": None,
         "layers": [{"material_id": "ffffffff-0000-0000-0000-000000000000", "thickness_m": 0.2}]},
        store,
    )
    assert problems and "ffffffff" in problems[0]

    _, problems = cm.validate_construction(
        {"name": "x", "design_usage": None,
         "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": -1}]},
        store,
    )
    assert any("thickness" in problem.lower() for problem in problems)
```

Append to `tests/test_gui_server.py` (endpoints; extend api fixture's GuiState with constructions dir injection):

```python
@pytest.fixture
def constructions_api(api, tmp_path, monkeypatch):
    import materialsdb.construction as cm

    server, state = api
    monkeypatch.setattr(cm, "constructions_dir", lambda: tmp_path / "constr")
    return server, state


def test_construction_crud_endpoints(constructions_api):
    server, state = constructions_api
    token = state.token
    body = {
        "name": "My wall", "design_usage": "consDesignForWall",
        "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}],
    }

    from urllib.parse import quote

    status, payload = request(server, "POST", f"/api/constructions/{quote('My wall')}", payload=body, token=token)
    assert status == 200

    status, payload = request(server, "GET", "/api/constructions")
    assert payload["constructions"] == ["My wall"]

    status, payload = request(server, "GET", f"/api/constructions/{quote('My wall')}")
    assert payload["name"] == "My wall"

    status, payload = request(server, "POST", "/api/u_value",
                              payload={"construction": body, "preset": "ISO6946"}, token=token)
    assert status == 200 and payload["u"] > 0

    status, payload = request(server, "DELETE", f"/api/constructions/{quote('My wall')}", token=token)
    assert status == 200


def test_construction_validation_error_surfaces(constructions_api):
    server, state = constructions_api
    status, payload = request(
        server, "POST", "/api/constructions/bad",
        payload={"name": "bad", "layers": [{"material_id": "nope", "thickness_m": 0.1}]},
        token=state.token,
    )
    assert status == 400
    assert "nope" in payload["error"]
```

Note: URL path contains a space ("My wall") - http.client handles it if quoted: use `urllib.parse.quote(name)` in paths or keep test names slug-free (e.g. "my-wall"). DECISION: endpoints accept both raw and slugged names (load_construction tries exact name then slug lookup); tests use "My wall" but pass through quote() in request helper - add `from urllib.parse import quote` and wrap path segments containing user data.

Run: FAIL (routes 404, helpers missing).

- [ ] **Step 2: Implement persistence + routes**

Persistence helpers appended to `construction.py`:

```python
import json
import re
import time

from materialsdb import cache


def constructions_dir():
    directory = cache.get_cache_folder() / "constructions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "construction"


def _unique_path(directory: Path, base: str) -> Path:
    candidate = directory / f"{base}.json"
    index = 2
    while candidate.exists():
        candidate = directory / f"{base}-{index}.json"
        index += 1
    return candidate


def validate_construction(body: dict, store_) -> tuple[Construction, list[str]]:
    problems: list[str] = []
    name = str(body.get("name") or "").strip()
    if not name:
        problems.append("name required")
    layers_body = body.get("layers")
    if not isinstance(layers_body, list) or not layers_body:
        problems.append("at least one layer required")
        layers_body = []
    layers = []
    for index, entry in enumerate(layers_body):
        if not isinstance(entry, dict):
            problems.append(f"layer {index}: invalid entry")
            continue
        material_id = str(entry.get("material_id") or "")
        try:
            thickness = float(entry.get("thickness_m"))
        except (TypeError, ValueError):
            problems.append(f"layer {index}: thickness must be a number")
            continue
        if not math.isfinite(thickness) or thickness <= 0:
            problems.append(f"layer {index}: thickness must be > 0")
            continue
        if store_.get(material_id) is None:
            problems.append(f"unknown material id: {material_id}")
            continue
        layers.append(ConstructionLayer(material_id=material_id, thickness_m=thickness))
    design_usage = body.get("design_usage") or None
    if design_usage not in (None, *_DESIGN_USAGE_TO_DIRECTION):
        problems.append(f"invalid design_usage: {design_usage}")
    return Construction(name=name, design_usage=design_usage, layers=layers), problems


def save_construction(construction: Construction, store_) -> Path:
    _, problems = validate_construction(_to_body(construction), store_)
    if problems:
        raise ValueError("; ".join(problems))
    directory = constructions_dir()
    base = slugify(construction.name)
    path = _unique_path(directory, base)
    payload = {
        "name": construction.name,
        "design_usage": construction.design_usage,
        "layers": [{"material_id": layer.material_id, "thickness_m": layer.thickness_m}
                   for layer in construction.layers],
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def _to_body(construction: Construction) -> dict:
    return {"name": construction.name, "design_usage": construction.design_usage,
            "layers": [{"material_id": layer.material_id, "thickness_m": layer.thickness_m}
                       for layer in construction.layers]}


def load_construction(name_or_slug: str, store_) -> Construction | None:
    directory = constructions_dir()
    candidates = [directory / f"{slugify(name_or_slug)}.json"]
    for file in directory.glob("*.json"):
        data = json.loads(file.read_text(encoding="utf-8"))
        if data.get("name") == name_or_slug:
            candidates.insert(0, file)
            break
    for file in candidates:
        if file.exists():
            data = json.loads(file.read_text(encoding="utf-8"))
            layers = [ConstructionLayer(material_id=l["material_id"], thickness_m=float(l["thickness_m"]))
                      for l in data.get("layers", [])]
            return Construction(name=data["name"], design_usage=data.get("design_usage"), layers=layers)
    return None


def list_constructions() -> list[str]:
    names = []
    for file in constructions_dir().glob("*.json"):
        try:
            names.append(json.loads(file.read_text(encoding="utf-8"))["name"])
        except (json.JSONDecodeError, KeyError):
            continue
    return sorted(names)


def delete_construction(name_or_slug: str) -> bool:
    file = constructions_dir() / f"{slugify(name_or_slug)}.json"
    if file.exists():
        file.unlink()
        return True
    return False
```

Add `from pathlib import Path` import at top of construction.py if missing.

Server routes in do_GET/do_POST/do_DELETE (new method):

```python
# do_GET additions (before generic 404):
if parsed.path == "/api/constructions":
    from materialsdb import construction as cm
    self._send(200, {"constructions": cm.list_constructions()})
    return
if parsed.path.startswith("/api/constructions/"):
    from materialsdb import construction as cm
    name = urllib.parse.unquote(parsed.path.rsplit("/", 1)[1])
    construction = cm.load_construction(name, self.state.resolve_store())
    if construction is None:
        self._send(404, {"error": f"unknown construction: {name}"})
    else:
        self._send(200, cm._to_body(construction))
    return

# do_POST additions inside authorized dispatch:
elif parsed.path == "/api/u_value":
    self._u_value(store_, payload)
elif parsed.path.startswith("/api/constructions/"):
    self._construction_save(store_, parsed, payload)

# new methods:
def _construction_save(self, store_, parsed, payload):
    import urllib.parse as uparse
    from materialsdb import construction as cm

    name = uparse.unquote(parsed.path.rsplit("/", 1)[1])
    body = dict(payload)
    body.setdefault("name", name)
    construction, problems = cm.validate_construction(body, store_)
    if problems:
        self._send(400, {"error": "; ".join(problems)})
        return
    cm.save_construction(construction, store_)
    self._send(200, {"saved": construction.name})

def _u_value(self, store_, payload):
    import urllib.parse as uparse  # noqa kept minimal
    from materialsdb import construction as cm

    body = payload.get("construction") or {}
    construction, problems = cm.validate_construction(body, store_)
    if problems:
        self._send(400, {"error": "; ".join(problems)})
        return
    result = cm.u_value(construction, store_, preset=payload.get("preset") or "ISO6946")
    self._send(200, asdict(result))

# do_DELETE (new):
def do_DELETE(self):
    if not self._authorized():
        self._send(403, {"error": "forbidden"})
        return
    parsed = urlparse(self.path)
    if parsed.path.startswith("/api/constructions/"):
        import urllib.parse as uparse
        from materialsdb import construction as cm

        name = uparse.unquote(parsed.path.rsplit("/", 1)[1])
        if cm.delete_construction(name):
            self._send(200, {"deleted": True})
        else:
            self._send(404, {"error": f"unknown construction: {name}"})
        return
    self._send(404, {"error": "not found"})
```

(asdict already imported in server.py; drop the stray uparse import comment lines if unused in _u_value.)

- [ ] **Step 3: Verify + commit**

Full suite green; gates green.

```bash
git add src/materialsdb/construction.py src/materialsdb/gui/server.py tests/
git commit -m "feat: construction json library with crud and u-value endpoints"
```
(use explicit test paths per staging rule)

---

### Task 4: Frontend constructions page

**Files:**
- Create: `src/materialsdb/gui/static/constructions.html`
- Create: `src/materialsdb/gui/static/app-constructions.js`
- Modify: `src/materialsdb/gui/static/index.html` (nav link)
- Modify: `tests/test_gui_server.py` (serving tests)

**Interfaces:**
- Consumes: all Task 3 endpoints; existing token pattern
- Produces: working composer UI; nav entry on picker page.

- [ ] **Step 1: Serving tests first**

Append to `tests/test_gui_server.py`:

```python
def test_constructions_page_served(api):
    server, state = api
    status, body = request(server, "GET", "/constructions.html")
    assert status == 200
    assert b"MATERIALSDB_TOKEN" in body
    status, body = request(server, "GET", "/app-constructions.js")
    assert status == 200
```

The static-file guard from the picker page already returns 404 for missing files - tests fail until files exist.

- [ ] **Step 2: Serve the new static route**

In server.py do_GET add beside /app.js:

```python
        if parsed.path in ("/constructions.html",):
            html = (STATIC_DIR / "constructions.html").read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", self.state.token)
            self._send(200, content_type="text/html; charset=utf-8", raw=html.encode("utf-8"))
            return
        if parsed.path == "/app-constructions.js":
            js = (STATIC_DIR / "app-constructions.js").read_text(encoding="utf-8")
            self._send(200, content_type="text/javascript; charset=utf-8", raw=js.encode("utf-8"))
            return
```

- [ ] **Step 3: Write constructions.html**

Same head/style/TOKEN pattern as index.html. Body skeleton:

```html
<body>
<div style="display:flex;gap:1rem;margin:1rem;height:calc(100vh - 2rem)">
 <div id="saved" style="width:14rem;border-right:1px solid #ccc;padding-right:.6rem">
  <h3>Constructions</h3>
  <ul id="saved-list" style="list-style:none;padding:0"></ul>
  <button id="new">new</button><button id="delete">delete</button>
  <hr><a href="/">picker</a>
 </div>
 <div id="editor" style="flex:2;display:flex;flex-direction:column">
  <div>
   <input id="name" placeholder="construction name">
   <select id="design-usage">
    <option value="">generic</option>
    <option value="consDesignForWall">wall</option>
    <option value="consDesignForRoof">roof</option>
    <option value="consDesignForFloor">floor</option>
   </select>
   <button id="add-layer">add layer</button>
  </div>
  <table style="margin-top:.5rem"><thead><tr>
    <th>#</th><th>material</th><th>thickness mm</th><th>lambda</th><th>r m2K/W</th><th></th>
  </tr></thead><tbody id="layers"></tbody></table>
  <div style="margin-top:.4rem">
   <button data-move="up">up</button><button data-move="down">down</button>
   <button data-action="remove">remove selected</button>
  </div>
 </div>
 <div id="ucard" style="width:16rem;border-left:1px solid #ccc;padding-left:.6rem">
  <h3>U-value</h3>
  <select id="preset"><option>ISO6946</option><option>SIA180</option></select>
  <div id="u-display" style="font-size:1.6rem">-</div>
  <div class="label" style="margin-top:.3rem">LAYERS</div>
  <dl id="contributions"></dl>
  <div id="warnings" style="color:#b60"></div>
  <hr>
  <button id="save">save</button>
  <button id="export-ifc">export .ifc</button>
  <button id="append-session">append to session</button>
  <p id="status"></p>
 </div>
</div>
<script>window.MATERIALSDB_TOKEN = "__TOKEN__";</script>
<script src="/app-constructions.js"></script>
</body>
```

- [ ] **Step 4: Write app-constructions.js**

State + behaviours (complete file ~200 lines):

```javascript
const TOKEN = window.MATERIALSDB_TOKEN;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

let layers = [];          // [{material_id, thickness_m}]
let selectedRow = -1;
let lastResult = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-MaterialsDB-Token": TOKEN },
  });
  const type = response.headers.get("Content-Type") || "";
  if (!response.ok) {
    const message = type.includes("json") ? (await response.json()).error : response.statusText;
    throw new Error(message);
  }
  return type.includes("json") ? response.json() : response.blob();
}

function renderLayers() {
  const tbody = $("layers");
  tbody.innerHTML = "";
  layers.forEach((layer, index) => {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    if (index === selectedRow) tr.style.background = "#eef";
    tr.innerHTML = `<td>${index + 1}</td><td data-role="name">${esc(layer.display_name || layer.material_id)}</td>` +
      `<td><input type="number" step="1" min="1" value="${Math.round(layer.thickness_m * 1000)}" data-index="${index}" style="width:5rem"> mm</td>` +
      `<td data-role="lambda">${esc(layer.lambda_value ?? "")}</td><td data-role="r">${esc(fmtR(index))}</td><td></td>`;
    tr.addEventListener("click", () => { selectedRow = index; renderLayers(); });
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("input[data-index]").forEach((input) => {
    input.addEventListener("change", () => {
      const mm = Number(input.value);
      if (!mm || mm <= 0) { setStatus("thickness must be > 0"); input.focus(); return; }
      layers[Number(input.dataset.index)].thickness_m = mm / 1000;
      refreshU();
    });
  });
}

function fmtR(index) {
  return lastResult && lastResult.contributions[index] ? lastResult.contributions[index].r.toFixed(3) : "";
}

function renderContributions() {
  if (!lastResult) { $("u-display").textContent = "-"; $("contributions").innerHTML = ""; $("warnings").textContent = ""; return; }
  if (lastResult.u === null) {
    $("u-display").textContent = "?";
    $("warnings").textContent = "layers without lambda: " + lastResult.missing_lambda_ids.length;
  } else {
    $("u-display").textContent = lastResult.u.toFixed(3) + " W/m2K";
    $("warnings").textContent = lastResult.missing_lambda_ids.length ? `warning: ${lastResult.missing_lambda_ids.length} layer(s) without lambda excluded` : "";
  }
  $("contributions").innerHTML = lastResult.contributions.map((c) =>
    `<dt>${esc(c.name || c.material_id.slice(0, 8))} \u2014 ${c.d_m.toFixed(3)} m</dt><dd>R = ${c.r.toFixed(3)}</dd>`).join("");
}

async function refreshU() {
  if (!layers.length) { lastResult = null; renderContributions(); return; }
  const body = {
    construction: {
      name: $("name").value || "draft",
      design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })),
    },
    preset: $("preset").value,
  };
  try {
    lastResult = await api("/api/u_value", { method: "POST", body: JSON.stringify(body) });
    // merge resolved names/lambdas back into editor rows for display
    lastResult.contributions.forEach((c) => {
      const row = layers.find((l) => l.material_id === c.material_id);
      if (row) { row.display_name = c.name || row.display_name; row.lambda_value = c.lambda_value; }
    });
  } catch (err) { setStatus(err.message); }
  renderLayers();
  renderContributions();
}

async function loadList() {
  const { constructions } = await api("/api/constructions");
  $("saved-list").innerHTML = constructions.map((name) =>
    `<li><a href="#" data-name="${esc(name)}">${esc(name)}</a></li>`).join("") || "<li><i>none saved</i></li>";
  $("saved-list").querySelectorAll("a").forEach((a) => a.addEventListener("click", async (event) => {
    event.preventDefault();
    const construction = await api(`/api/constructions/${encodeURIComponent(a.dataset.name)}`);
    $("name").value = construction.name;
    $("design-usage").value = construction.design_usage || "";
    layers = construction.layers;
    selectedRow = -1; lastResult = null;
    await refreshU();
  }));
}

function setStatus(text) { $("status").textContent = text; return text; }
// NOTE: keep this declaration ABOVE openChooser/its handlers in final file order.

$("new").onclick = () => { layers = []; selectedRow = -1; lastResult = null; $("name").value = ""; renderLayers(); renderContributions(); };
$("delete").onclick = async () => {
  const name = $("name").value; if (!name) return setStatus("nothing loaded");
  await api(`/api/constructions/${encodeURIComponent(name)}`, { method: "DELETE" });
  setStatus(`deleted ${name}`); loadList(); $("new").onclick();
};
/* --- layer chooser modal over /api/materials --- */
function openChooser() {
  const overlay = document.createElement("div");
  overlay.id = "chooser";
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center";
  overlay.innerHTML = `<div style="background:#fff;padding:.75rem;width:26rem;max-height:80vh;display:flex;flex-direction:column">` +
    `<input id="chooser-search" placeholder="live search…" style="margin-bottom:.4rem">` +
    `<div id="chooser-results" style="overflow:auto;flex:1"></div>` +
    `<button id="chooser-close" style="margin-top:.4rem">close</button></div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector("#chooser-close").onclick = close;
  overlay.addEventListener("click", (event) => { if (event.target === overlay) close(); });

  const resultsBox = overlay.querySelector("#chooser-results");
  let debounceTimer;
  const runSearch = async () => {
    const needle = overlay.querySelector("#chooser-search").value.trim();
    const { materials } = await api(`/api/materials${needle ? `?text=${encodeURIComponent(needle)}` : ""}`);
    materials.splice(60);   // cap DOM size; refine search for more
    resultsBox.innerHTML = materials.map((m) =>
      `<div class="chooser-row" data-id="${esc(m.id)}" style="cursor:pointer;padding:.15rem;border-bottom:1px solid #eee">` +
      `<b>${esc(m.display_name)}</b> · ${esc(m.company)} · ${esc(m.type)}` +
      `${m.lambda_min !== null ? ` · λ ${m.lambda_min}` : ""}</div>`).join("") ||
      `<i style="color:#888">no match</i>`;
    resultsBox.querySelectorAll(".chooser-row").forEach((row) => {
      row.addEventListener("click", () => {
        const materialId = row.dataset.id;
        if (layers.some((l) => l.material_id === materialId)) { setStatus("material already in construction"); return; }
        layers.push({ material_id: materialId, thickness_m: 0.2 });
        selectedRow = layers.length - 1;
        close();
        refreshU();
      });
    });
  };
  overlay.querySelector("#chooser-search").addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runSearch, 250);
  });
  runSearch();
}

$("add-layer").onclick = openChooser;
document.querySelector("[data-move=up]").onclick = () => {
  if (selectedRow > 0) { [layers[selectedRow - 1], layers[selectedRow]] = [layers[selectedRow], layers[selectedRow - 1]]; selectedRow -= 1; renderLayers(); refreshU(); }
};
document.querySelector("[data-move=down]").onclick = () => {
  if (selectedRow > -1 && selectedRow < layers.length - 1) { [layers[selectedRow + 1], layers[selectedRow]] = [layers[selectedRow], layers[selectedRow + 1]]; selectedRow += 1; renderLayers(); refreshU(); }
};
document.querySelector("[data-action=remove]").onclick = () => {
  if (selectedRow > -1) { layers.splice(selectedRow, 1); selectedRow = -1; renderLayers(); refreshU(); }
};
$("preset").onchange = refreshU;
$("design-usage").onchange = refreshU;
$("save").onclick = async () => {
  const name = $("name").value.trim(); if (!name) return setStatus("name required");
  if (!layers.length) return setStatus("add at least one layer");
  await api(`/api/constructions/${encodeURIComponent(name)}`, { method: "POST",
    body: JSON.stringify({ name, design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })) }) });
  setStatus(`saved ${name}`); loadList();
};
$("export-ifc").onclick = async () => {
  const name = $("name").value.trim(); if (!name || !layers.length) return setStatus("nothing to export");
  const blob = await api("/api/export-construction", { method: "POST",
    body: JSON.stringify({ construction: { name, design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })) } }) });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a"); link.href = url; link.download = `${name}.ifc`; link.click();
  URL.revokeObjectURL(url);
};
$("append-session").onclick = async () => {
  if (!$("name").value || !layers.length) return setStatus("nothing to append");
  const result = await api("/api/append-construction", { method: "POST",
    body: JSON.stringify({ construction: { name: $("name").value,
      design_usage: $("design-usage").value || null,
      layers: layers.map((l) => ({ material_id: l.material_id, thickness_m: l.thickness_m })) } }),
    });
  setStatus(`appended layer set (${result.layer_count} layers)`);
};

loadList();
```

MISSING ENDPOINTS this frontend expects -> implement NOW as part of this task (server.py):

```python
# POST /api/export-construction {construction} ->
#     validate via cm.validate_construction (400 on problems)
#     file = cm.to_ifc_layer_set(construction, store_) ; tempfile write->bytes->unlink
#     respond application/ifc attachment filename="<slug>.ifc"
# POST /api/append-construction {construction} -> 409 if no session ;
#     file = cm.to_ifc_layer_set(...) ; then for each IfcMaterialLayer in
#     file.by_type("IfcMaterialLayer"): create matching IfcMaterialLayer into
#     self.state.file referencing the SAME IfcMaterial entities? NO -
#     cross-file entity reuse is invalid. Instead: rebuild the layer set inside
#     the session file via builder (same loop as to_ifc_layer_set but targeting
#     state.file), i.e. call cm.to_ifc_layer_set(construction, store_, file=self.state.file)
#     after validating, then count layers: {"layer_count": len(...)}.
```

SIMPLIFICATION for append: extend `to_ifc_layer_set(construction, store_, file=None)` so passing an EXISTING session file skips wrapper creation and appends directly into it (already supported by the `file=` parameter!) - therefore `/api/append-construction` is:

```python
def _append_construction(self, store_, payload):
    if self.state.file is None:
        self._send(409, {"error": "no session open"})
        return
    from materialsdb import construction as cm

    construction, problems = cm.validate_construction(payload.get("construction") or {}, store_)
    if problems:
        self._send(400, {"error": "; ".join(problems)})
        return
    before = len(self.state.file.by_type("IfcMaterialLayerSet"))
    cm.to_ifc_layer_set(construction, store_, file=self.state.file)
    after = self.state.file.by_type("IfcMaterialLayerSet")
    self._send(200, {"layer_count": len(after[-1].MaterialLayers)})
```

and `/api/export-construction` mirrors `_export`'s tempfile response using `cm.to_ifc_layer_set(construction, store_)`.

Add both routes to the authorized POST dispatch.

- [ ] **Step 5: Picker page nav link**

In index.html toolbar div prepend:

```html
<a href="/constructions.html" style="margin-right:.75rem">constructions \u2192</a>
```

(plain text arrow, no unicode escape needed inside HTML.)

- [ ] **Step 6: Verify + commit**

```bash
node --check src/materialsdb/gui/static/app-constructions.js
python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -q && python3 -m pytest -p no:pytest-blender -q
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples && ruff format --exclude src/materialsdb/classes.py --check . ; echo format-exit=$?
ty check --project .
git add src/materialsdb/gui/ tests/test_gui_server.py
git commit -m "feat: constructions composer ui with live u-value and ifc export"
```

(format exit may be 1 from untracked scratch only - verify with git status.)

Manual checklist for report: create construction, add 2 layers via guid prompt, reorder, edit thickness -> U updates live without clicks beyond change events; save appears in list; reload restores; export opens as valid IFC; append into opened session adds one IfcMaterialLayerSet.

---

### Task 5: README + final gates

**Files:**
- Modify: `README.md`

**Interfaces:** docs only.

- [ ] **Step 1: README section**

Insert after the `# Material picker GUI :` section:

```markdown
# Construction maker :
Compose thermal constructions from materialsdb materials and compute their
U-value (ISO 6946 / SIA 180 surface resistance presets):

```bash
PYTHONPATH=src python3 -m materialsdb.gui   # then open constructions.html
```

Create a construction, add materials from the catalog, adjust layer
thicknesses in millimetres and read the resulting U-value live. Save
constructions as JSON in your cache directory, export them as standalone
`.ifc` files containing an `IfcMaterialLayerSet`, or append them into an
already-open session file.
```

(Nest fences per existing convention.)

- [ ] **Step 2: Final verification**

All four gates green; full suite green (expected 60+ tests); node --check both JS files.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: construction maker readme section"
```
