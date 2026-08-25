# Single-Material IFC Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create one material's IFC representation on demand - appended idempotently into an existing ifcopenshell file or as a minimal standalone file - with batch and single export sharing one builder code path.

**Architecture:** New `src/materialsdb/ifc/material_builder.py` owns all per-material entity logic (IfcMaterial, property sets, surface style via name-keyed cache, layer sets, `materialsdb` identity pset). `ProjectLibrary.create_materials` delegates to it; behavior is locked by a Task 1 characterization test. Public API: `material_builder.add_material(file, material, ...)` and `material_builder.create_material_file(material_id)`; a new `MaterialStore.get_summary` supplies company info for standalone files.

**Tech Stack:** ifcopenshell 0.8.x (`[ifc]` extra), existing lxml dataclasses, sqlite store, pytest.

## Global Constraints

- Single-path entity scope: IfcMaterial + psets + surface style + IfcMaterialLayer(Set) when thickness exists. NO usage type entities outside the batch path (spec decision).
- Identity: `materialsdb` IfcMaterialProperties pset carrying `id`, `company_id`, `company`, plus `verxml` only when known. Append reuses an existing material whose identity id matches; never duplicates.
- Batch behavior must not change: Task 1's characterization inventory test stays green through every refactor.
- Surface style resolution must be O(1) per lookup via a name-keyed dict on MaterialBuilder, replacing per-material `by_type("IfcSurfaceStyle")` scans.
- `replace=True` purges only material-owned entities (its IfcMaterialProperties psets, its IfcMaterialLayer + emptied parent IfcMaterialLayerSet); shared styles are never deleted.
- Unknown material id -> KeyError naming the id.
- Tooling gates stay green after every task: ruff check/format and ty exit 0 (commands below), suite green via `python3 -m pytest -p no:pytest-blender`.
- IFC test modules start with `pytest.importorskip("ifcopenshell")` (CI installs [ifc]; core-only envs stay green).
- Commit style: short lowercase imperative.
- Never touch untracked local files or tests/_stale_materialsdb.disabled.

## Verification commands (every task)

```bash
python3 -m pytest -p no:pytest-blender -q
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples && echo LINT-CLEAN
ruff format --exclude src/materialsdb/classes.py --check . && echo FORMAT-CLEAN
ty check --project . && echo TY-CLEAN
```

## Key current signatures (orientation)

```python
# store.py
MaterialStore.get(material_id) -> Material | None
_MATERIAL_COLUMNS  # explicit column tuple, 'xml' last
# utils.py
get_material_name(material, lang) / get_material_description(material, lang)
get_material_layers(material) -> Generator[Layer]; get_by_country(values, country)
new_tdatetime() -> TDateTime  # current time as xml days
# project_library.py (today)
ProjectLibrary(schema="IFC4"); .create_application(); .create_project_library(source: Materials)
.create_materials(source); .get_surface_style(color, category); .color_xml_to_ifc(color)
CATEGORIES, PSETS (already cleaned), clean_psets(psets), get_value(layer, definition, country)
# classes.Material has .id/.information but NOT verXML (lives on Materials root)
```

---

### Task 1: Characterization test locking current batch export

**Files:**
- Create: `tests/test_material_builder.py`

**Interfaces:**
- Consumes: unmodified `project_library.ProjectLibrary`
- Produces: `_entity_inventory(file)` helper + pinned test `test_batch_export_inventory_is_stable`; later tasks must keep it green.

- [ ] **Step 1: Write the characterization test**

Create `tests/test_material_builder.py`:

```python
import pytest

pytest.importorskip("ifcopenshell")

from materialsdb.ifc.project_library import ProjectLibrary


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


def _entity_inventory(file):
    inventory = {}
    for entity in file:
        inventory[entity.is_a()] = inventory.get(entity.is_a(), 0) + 1
    return inventory


def test_batch_export_inventory_is_stable(mini_source):
    library = ProjectLibrary()
    library.create_project_library(mini_source)
    library.create_materials(mini_source)

    print(_entity_inventory(library.file))  # keep while pinning values

    inventory = _entity_inventory(library.file)
    assert inventory["IfcMaterial"] == 3  # one per layer across fixture materials
    assert inventory["IfcProjectLibrary"] == 1
    material_names = sorted(m.Name for m in library.file.by_type("IfcMaterial"))
    assert material_names == ["Beton B", "Isolant A", "Isolant A"]
```

IMPORTANT pinning procedure: run it once, read the printed real inventory, then pin ALL remaining assertions to observed reality (surface styles count, styled items, representation contexts, property-set totals). The committed assertions must reflect CURRENT behavior exactly - this test is the refactor safety net, not a spec of desired behavior.

- [ ] **Step 2: Run against CURRENT code**

Run: `python3 -m pytest -p no:pytest-blender tests/test_material_builder.py -v`
Expected: PASS with pinned real-world values (golden snapshot).

- [ ] **Step 3: Commit**

```bash
git add tests/test_material_builder.py
git commit -m "test: pin batch export entity inventory before builder refactor"
```

---

### Task 2: Extract MaterialBuilder; ProjectLibrary delegates

**Files:**
- Create: `src/materialsdb/ifc/material_builder.py`
- Modify: `src/materialsdb/ifc/project_library.py`
- Modify: `tests/test_material_builder.py`

**Interfaces:**
- Consumes: CATEGORIES / PSETS / clean_psets / get_value (moving from project_library), utils.*, config.get_lang/get_country
- Produces (used by Tasks 3-5):

```python
MATERIALSDB_PSET = "materialsdb"

class MaterialBuilder:
    def __init__(self, file, country=None, lang=None): ...
    def build(self, material, company_id="", company="", verxml=None) -> list
        # returns the created IfcMaterial entities, one per layer (empty if no layers)
    def find_existing(self, material_id) -> entity | None   # identity-pset lookup
    def get_surface_style(self, color, category)            # cached, O(1)
    def color_xml_to_ifc(self, color)

def add_material(file, material, company_id="", company="", verxml=None,
                 replace=False) -> entity | None
    # idempotent append into any ifcopenshell file; primary (first) material returned
```

- [ ] **Step 1: Write failing builder tests**

Append to `tests/test_material_builder.py`:

```python
import ifcopenshell

from materialsdb.ifc.material_builder import (
    MATERIALSDB_PSET,
    MaterialBuilder,
)


def _identity_id(file, material_entity):
    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != MATERIALSDB_PSET:
            continue
        materials = pset.Material
        if not isinstance(materials, (list, tuple)):
            materials = [materials]
        if material_entity not in materials:
            continue
        for prop in pset.Properties:
            if prop.Name == "id":
                return prop.NominalValue.wrappedValue
    return None


def test_build_creates_identity_pset_and_materials(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    builder = MaterialBuilder(file)
    material = mini_source.material[0]

    created = builder.build(
        material, company_id="A1B85A67", company="Mini SA", verxml=3
    )

    assert len(created) == 2  # two layers -> two IfcMaterial entities
    for entity in created:
        assert _identity_id(file, entity) == str(material.id)
    names = {p.Name for e in created for p in file.get_inverse(e) if p.is_a("IfcMaterialProperties")}
    assert MATERIALSDB_PSET in names


def test_build_without_layers_creates_nothing(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    builder = MaterialBuilder(file)

    created = builder.build(mini_source.material[2], company="Mini SA")

    assert created == []
    assert len(file.by_type("IfcMaterial")) == 0


def test_find_existing_roundtrip(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    builder = MaterialBuilder(file)
    material = mini_source.material[1]
    created = builder.build(material, company="Mini SA")

    assert builder.find_existing(str(material.id)) is created[0]
    assert builder.find_existing("unknown-id") is None


def test_style_cache_bounds_styles(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    builder = MaterialBuilder(file)
    for material in mini_source.material:  # same category/color repeatedly
        for _ in range(3):
            builder.build(material, company="Mini SA")

    # Insulation color + Concrete fallback + Others(no color on mat 3 unused) bounded
    assert len(file.by_type("IfcSurfaceStyle")) <= 3
```

Run: `python3 -m pytest -p no:pytest-blender tests/test_material_builder.py -v`
Expected: NEW tests FAIL with ModuleNotFoundError; characterization test still passes.

- [ ] **Step 2: Create material_builder.py**

Create `src/materialsdb/ifc/material_builder.py`:

```python
"""Shared per-material IFC entity creation for batch and single export."""
import json
from pathlib import Path

import ifcopenshell

from materialsdb import config, utils

MATERIALSDB_PSET = "materialsdb"

CATEGORIES = {
    "Others": {"hatch": "", "color": (255, 255, 255)},
    "Water_Proof": {"hatch": "", "color": (255, 255, 255)},
    "Vapour_Proof": {"hatch": "", "color": (0, 0, 0)},
    "Concrete": {"hatch": "", "color": (0, 255, 0)},
    "Wood_Timberproducts": {"hatch": "", "color": (91, 60, 17)},
    "Insulation": {"hatch": "", "color": (253, 108, 158)},
    "Masonry": {"hatch": "", "color": (253, 70, 38)},
    "Metal": {"hatch": "", "color": (119, 181, 254)},
    "Mortar": {"hatch": "", "color": (102, 0, 153)},
    "Plastics": {"hatch": "", "color": (96, 96, 96)},
    "Stone": {"hatch": "", "color": (0, 0, 255)},
    "Composite": {"hatch": "", "color": (112, 141, 35)},
    "Films": {"hatch": "", "color": (0, 0, 0)},
    "Render": {"hatch": "", "color": (0, 0, 0)},
    "Covering": {"hatch": "", "color": (0, 0, 0)},
    "Glas": {"hatch": "", "color": (27, 79, 8)},
    "Soil": {"hatch": "", "color": (142, 84, 52)},
}
```

```python
def clean_psets(psets):
    new_dict = {}
    for pset_name, props in psets.items():
        pset_dict = {}
        for prop_name, definition in props.items():
            if definition["path"]:
                pset_dict[prop_name] = definition
        if pset_dict:
            new_dict[pset_name] = pset_dict
    return new_dict


PSETS = json.loads(Path(__file__).with_name("material_psets.json").read_text("utf-8"))
PSETS = clean_psets(PSETS)


def get_value(layer, definition, country=None):
    value = layer
    for attrib in definition["path"]:
        value = getattr(value, attrib)
        if isinstance(value, list):
            value = utils.get_by_country(value, country)
            if not value:
                return None
    return value


class MaterialBuilder:
    def __init__(self, file, country=None, lang=None):
        self.file = file
        self.country = country or config.get_country()
        self.lang = lang or config.get_lang()
        self._context = None
        self._styles = {}

    def build(self, material, company_id="", company="", verxml=None):
        name = utils.get_material_name(material, self.lang)
        description = utils.get_material_description(material, self.lang)
        category = str(material.information.group or "")
        color = material.information.color
        surface_style = self.get_surface_style(color, category)
        styled_item = self.file.createIfcStyledItem(Styles=[surface_style])
        self.file.createIfcStyledRepresentation(
            ContextOfItems=self._get_context(),
            RepresentationIdentifier="Body",
            Items=[styled_item],
        )
        created = []
        for layer in utils.get_material_layers(material):
            ifc_material = self.file.createIfcMaterial(str(name), str(description), category)
            created.append(ifc_material)
            self._create_identity_pset(ifc_material, material, company_id, company, verxml)
            self._create_property_psets(ifc_material, layer)
            geometry = utils.get_by_country(layer.geometry or (), self.country)
            thick = getattr(geometry, "thick", None)
            if thick:
                element_name = f"{name} | {thick}mm"
                ifc_layer = self.file.create_entity(
                    "IfcMaterialLayer",
                    Material=ifc_material,
                    LayerThickness=thick / 1000,
                    Name=str(element_name),
                )
                self.file.create_entity(
                    "IfcMaterialLayerSet",
                    MaterialLayers=[ifc_layer],
                    LayerSetName=str(element_name),
                )
        return created

    def find_existing(self, material_id):
        for pset in self.file.by_type("IfcMaterialProperties"):
            if pset.Name != MATERIALSDB_PSET:
                continue
            if self._pset_id(pset) == str(material_id):
                materials = pset.Material
                if not isinstance(materials, (list, tuple)):
                    materials = [materials]
                return materials[0]
        return None

    @staticmethod
    def _pset_id(pset):
        for prop in pset.Properties:
            if prop.Name == "id":
                return prop.NominalValue.wrappedValue
        return None

    def _get_context(self):
        if self._context is None:
            self._context = self.file.createIfcRepresentationContext()
        return self._context

    def _create_identity_pset(self, ifc_material, material, company_id, company, verxml):
        text = lambda v: _ifc_text(self.file, v)
        properties = [
            self.file.create_entity(
                "IfcPropertySingleValue", Name="id", NominalValue=text(material.id)
            ),
            self.file.create_entity(
                "IfcPropertySingleValue", Name="company_id", NominalValue=text(company_id)
            ),
            self.file.create_entity(
                "IfcPropertySingleValue", Name="company", NominalValue=text(company)
            ),
        ]
        if verxml is not None:
            properties.append(
                self.file.create_entity(
                    "IfcPropertySingleValue",
                    Name="verxml",
                    NominalValue=self.file.create_entity("IfcInteger", int(verxml)),
                )
            )
        self.file.create_entity(
            "IfcMaterialProperties",
            Name=MATERIALSDB_PSET,
            Properties=properties,
            Material=ifc_material,
        )

    def _create_property_psets(self, ifc_material, layer):
        for pset_name, props in PSETS.items():
            properties = []
            for prop_name, definition in props.items():
                primary_measure_type = definition["primary_measure_type"]
                if not primary_measure_type:
                    continue
                value = get_value(layer, definition, self.country)
                if value:
                    unit_factor = definition.get("unit_factor", None) or 1
                    properties.append(
                        self.file.create_entity(
                            "IfcPropertySingleValue",
                            Name=prop_name,
                            NominalValue=self.file.create_entity(primary_measure_type, value * unit_factor),
                        )
                    )
            if not properties:
                continue
            self.file.create_entity(
                "IfcMaterialProperties",
                Name=pset_name,
                Properties=properties,
                Material=ifc_material,
            )

    def get_surface_style(self, color, category):
        if color:
            name = f"color {color}"
        else:
            if not category or category not in CATEGORIES:
                category = "Others"
            name = f"category {category}"
        if name in self._styles and self._styles[name] in self.file:
            return self._styles[name]
        if color:
            style = self.file.createIfcSurfaceStyleShading(SurfaceColour=self.color_xml_to_ifc(color))
        else:
            style = self.file.createIfcSurfaceStyleShading(
                SurfaceColour=self.file.createIfcColourRgb(None, *CATEGORIES[category]["color"]),
            )
        surface_style = self.file.createIfcSurfaceStyle(Name=name, Side="BOTH", Styles=[style])
        self._styles[name] = surface_style
        return surface_style

    def color_xml_to_ifc(self, color: int):
        """Color definition in xml is obscur. We assume that it is a decimal color.
        See: https://stackoverflow.com/a/2262152/4098083"""
        return self.file.createIfcColourRgb(Blue=color & 255, Green=(color >> 8) & 255, Red=(color >> 16) & 255)


def add_material(file, material, company_id="", company="", verxml=None, replace=False):
    builder = MaterialBuilder(file)
    existing = builder.find_existing(str(material.id))
    if existing and not replace:
        return existing
    if existing and replace:
        purge_material(file, existing)
    created = builder.build(material, company_id=company_id, company=company, verxml=verxml)
    return created[0] if created else None
```

NOTE on `purge_material`: it does not exist yet - Task 3 adds it to this module. For Task 2, guard `add_material`'s replace branch exactly as written but add at module level a temporary stub:

```python
def purge_material(file, material_entity):  # implemented in Task 3
    raise NotImplementedError
```

Add this module-level helper next to `clean_psets` (used by `_create_identity_pset`):

```python
def _ifc_text(file, value):
    return file.create_entity("IfcText", str(value))
```

Then replace the inner `text = lambda ...` line in `_create_identity_pset` with direct calls - final form of the property list uses `_ifc_text(self.file, ...)` inline:

```python
        properties = [
            self.file.create_entity(
                "IfcPropertySingleValue", Name="id", NominalValue=_ifc_text(self.file, material.id)
            ),
            self.file.create_entity(
                "IfcPropertySingleValue", Name="company_id", NominalValue=_ifc_text(self.file, company_id)
            ),
            self.file.create_entity(
                "IfcPropertySingleValue", Name="company", NominalValue=_ifc_text(self.file, company)
            ),
        ]
```

- [ ] **Step 3: Refactor project_library.py to delegate**

In `src/materialsdb/ifc/project_library.py`:
1. Delete CATEGORIES, clean_psets, PSETS, get_value definitions; import them instead:

```python
from materialsdb.ifc.material_builder import (
    CATEGORIES,
    MATERIALSDB_PSET,
    PSETS,
    MaterialBuilder,
    clean_psets,
    get_value,
)
```

(keep re-exports so any external importer of project_library.PSETS still works; ruff F401 is satisfied because create_materials/get_surface_style still use them.)

2. Replace the body of `ProjectLibrary.__init__` additions: after existing lines add

```python
self.builder = MaterialBuilder(self.file, country=self.country, lang=self.lang)
```

3. Rewrite `create_materials` to delegate per-material work while KEEPING usage-type creation batch-only:

```python
def create_materials(self, source: Materials):
    file = self.file
    for material in utils.get_materials(source, self.country):
        created = self.builder.build(material)
        if not created:
            continue
        name = utils.get_material_name(material, self.lang)
        for layer, ifc_material in zip(utils.get_material_layers(material), created):
            geometry = utils.get_by_country(layer.geometry or (), self.country)
            thick = getattr(geometry, "thick", None)
            element_name = f"{name} | {thick}mm" if thick else name
            assigned_material = self._assigned_material(ifc_material, thick, element_name, name)
            self._create_usage_types(material, assigned_material, element_name)
```

with two small helpers added to ProjectLibrary:

```python
def _assigned_material(self, ifc_material, thick, element_name, name):
    if not thick:
        return ifc_material
    ifc_layer = self.file.create_entity(
        "IfcMaterialLayer",
        Material=ifc_material,
        LayerThickness=thick / 1000,
        Name=str(element_name),
    )
    return self.file.create_entity(
        "IfcMaterialLayerSet",
        MaterialLayers=[ifc_layer],
        LayerSetName=str(element_name),
    )

def _create_usage_types(self, material, assigned_material, element_name):
    file = self.file
    kind_map = (("wall", "IfcWallType"), ("roof", "IfcRoofType"), ("floor", "IfcRoofType"), ("door", "IfcDoorType"))
    for flag, entity_type in kind_map:
        if getattr(material.information, flag, None):
            product = file.create_entity(entity_type, GlobalId=ifcopenshell.guid.new(), Name=str(element_name))
            ifcopenshell.api.run(
                "material.assign_material", file, products=[product], material=assigned_material
            )
```

(This preserves the historical quirk that floor creates an IfcRoofType - characterization test pins whatever exists.)

4. Delete `get_surface_style`/`color_xml_to_ifc` methods ONLY if nothing else references them; if you keep thin delegating wrappers for compatibility:

```python
def get_surface_style(self, color, category):
    return self.builder.get_surface_style(color, category)
```

prefer the delegating wrapper.

- [ ] **Step 4: Verify all tests incl. characterization**

Run: `python3 -m pytest -p no:pytest-blender tests/test_material_builder.py tests/test_project_library.py -v`
Expected: ALL PASS - especially `test_batch_export_inventory_is_stable` unchanged. NOTE: inventory may legitimately differ ONLY by the added `materialsdb` identity pset properties/entities; if so, update the characterization test's pinned numbers and record in the task report WHY each number changed (identity pset entities are additive and expected). Any OTHER difference means behavior drift - STOP and investigate.

Then run the four Global Constraints verification commands. All must pass.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/ifc/ tests/test_material_builder.py
git commit -m "refactor: extract shared MaterialBuilder for batch and single export"
```

---

### Task 3: purge_material + replace=True

**Files:**
- Modify: `src/materialsdb/ifc/material_builder.py`
- Modify: `tests/test_material_builder.py`

**Interfaces:**
- Consumes: Task 2 module
- Produces:

```python
def purge_material(file, material_entity) -> None
    # removes, for the given IfcMaterial only:
    #   - every IfcMaterialProperties pset whose Material contains it
    #   - every IfcMaterialLayer whose Material is it, plus its parent
    #     IfcMaterialLayerSet once empty
    #   - the IfcMaterial entity itself (file.remove)
    # Shared IfcSurfaceStyle entities are NEVER removed.
```

- [ ] **Step 1: Write failing tests**

Append to `tests/test_material_builder.py` (extend the existing top import from material_builder with add_material):

```python
from materialsdb.ifc.material_builder import (
    MATERIALSDB_PSET,
    MaterialBuilder,
    add_material,
)


def test_add_material_is_idempotent(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    material = mini_source.material[0]

    first = add_material(file, material, company_id="A1B85A67", company="Mini SA", verxml=3)
    count_before = len(file.by_type("IfcMaterial"))
    second = add_material(file, material, company_id="A1B85A67", company="Mini SA", verxml=3)

    assert first is second
    assert len(file.by_type("IfcMaterial")) == count_before


def test_replace_rebuilds_with_fresh_global_ids(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    material = mini_source.material[0]

    old = add_material(file, material, company="Mini SA", verxml=3)
    old_ids = [m.GlobalId for m in file.by_type("IfcMaterial")]

    new = add_material(file, material, company="Mini SA", verxml=3, replace=True)

    assert new is not None and new.GlobalId not in old_ids
    assert _identity_id(file, new) == str(material.id)
    assert len(file.by_type("IfcMaterial")) == len(old_ids)  # same total, rebuilt


def test_purge_keeps_shared_styles(mini_source):
    from materialsdb.ifc.material_builder import purge_material

    file = ifcopenshell.file(schema="IFC4")
    builder = MaterialBuilder(file)
    a = builder.build(mini_source.material[0], company="Mini SA")  # Insulation color style
    b = builder.build(mini_source.material[1], company="Mini SA")  # Concrete category style

    styles_before = len(file.by_type("IfcSurfaceStyle"))
    purge_material(file, a[0])

    remaining = {m.GlobalId for m in file.by_type("IfcMaterial")}
    assert a[0].GlobalId not in remaining
    assert b[0].GlobalId in remaining
    assert len(file.by_type("IfcSurfaceStyle")) == styles_before  # shared styles untouched
```

Run: `python3 -m pytest -p no:pytest-blender tests/test_material_builder.py -v`
Expected: idempotency test PASSES already; replace/purge FAIL with NotImplementedError.

- [ ] **Step 2: Implement purge_material**

Replace the temporary stub in `material_builder.py` with:

```python
def _materials_of(pset):
    materials = pset.Material
    if not isinstance(materials, (list, tuple)):
        materials = [materials]
    return materials


def purge_material(file, material_entity) -> None:
    """Remove one IfcMaterial and everything the builder created for it.

    Shared surface styles are intentionally kept."""
    target = {m.id() for m in file.by_type("IfcMaterial") if m == material_entity}
    for pset in list(file.by_type("IfcMaterialProperties")):
        if {m.id() for m in _materials_of(pset)} & target:
            file.remove(pset)
    for layer in list(file.by_type("IfcMaterialLayer")):
        if layer.Material is not None and layer.Material.id() in target:
            layer_sets = getattr(layer, "ToMaterialLayerSet", None) or ()
            file.remove(layer)
            for parent in layer_sets:
                if not parent.MaterialLayers:
                    file.remove(parent)
    for material in [m for m in file.by_type("IfcMaterial") if m.id() in target]:
        file.remove(material)
```

Implementation notes:
- Compare entities by ifcopenshell guid (`entity.id()`), never by Python identity.
- `file.remove(entity)` exists on ifcopenshell.file and unlinks known inverses; iterate over fresh `list(...)` snapshots because removal mutates the registry.
- `layer.ToMaterialLayerSet` is the IFC4 inverse attribute name; verify interactively against the installed ifcopenshell 0.8 (`dir(layer)` filtered on 'LayerSet') and adapt the name if it differs, recording the finding in the task report.

- [ ] **Step 3: Verify + commit**

Run the three new tests plus all four Global Constraints verification commands. All green.

```bash
git add src/materialsdb/ifc/material_builder.py tests/test_material_builder.py
git commit -m "feat: idempotent single-material append with replace support"
```

---

### Task 4: Store summary lookup + standalone create_material_file

**Files:**
- Modify: `src/materialsdb/store.py`, `src/materialsdb/ifc/project_library.py`
- Modify: `tests/test_store.py`, `tests/test_material_builder.py`

**Interfaces:**
- Consumes: MaterialStore internals (`_MATERIAL_COLUMNS`, `_row_to_summary`), Task 2/3 module
- Produces:

```python
# store.py
def get_summary(self, material_id: str) -> MaterialSummary | None
    # same row as get(), returned as MaterialSummary

# project_library.py (signature change; batch caller updated in same task)
ProjectLibrary.create_project_library(self, company, companyid, ver, crd, role="MANUFACTURER")

# material_builder.py
def create_material_file(material_id, schema="IFC4", store_=None)
    # raises KeyError("Unknown materialsdb material id: <id>") when absent
    # returns ifcopenshell.file: wrapper + application stub + the material set
```

- [ ] **Step 1: Failing test for get_summary**

Append to `tests/test_store.py`:

```python
def test_get_summary_returns_row_summary(store):
    s = store.get_summary("00000000-0000-0000-0000-000000000001")

    assert s is not None
    assert s.id == "00000000-0000-0000-0000-000000000001"
    assert s.company == "Mini SA"
    assert s.company_id == "A1B85A67-5B1E-4960-A297-2DE8275049C5"
    assert store.get_summary("missing") is None
```

Run: `python3 -m pytest -p no:pytest-blender tests/test_store.py -v`
Expected: FAIL with AttributeError (no get_summary).

Implement in `store.py` right after `get`:

```python
    def get_summary(self, material_id: str) -> MaterialSummary | None:
        row = self.connection.execute(
            f"SELECT {', '.join(_MATERIAL_COLUMNS)} FROM materials WHERE id=?",
            (material_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_summary(row)
```

(If `_MATERIAL_COLUMNS` does not exist under that exact name, reuse whatever explicit column list `summaries()` selects - same columns, same order as `_row_to_summary` expects. Do not use `SELECT *`.)

- [ ] **Step 2: Failing test for create_material_file**

Append to `tests/test_material_builder.py`:

```python
def test_create_material_file_standalone(mini_xml, tmp_path):
    from materialsdb.ifc.material_builder import create_material_file
    from materialsdb.store import MaterialStore

    db_path = tmp_path / "standalone.db"
    store_ = MaterialStore(db_path=db_path)
    store_.refresh(paths=[mini_xml])

    file = create_material_file(
        "00000000-0000-0000-0000-000000000001", store_=store_
    )

    assert len(file.by_type("IfcMaterial")) == 2  # two layers of fixture material 1
    assert file.by_type("IfcProjectLibrary")
    out = tmp_path / "single.ifc"
    file.write(str(out))
    reopened = ifcopenshell.open(str(out))
    assert len(reopened.by_type("IfcMaterial")) == 2


def test_create_material_file_unknown_id(mini_xml, tmp_path):
    from materialsdb.ifc.material_builder import create_material_file
    from materialsdb.store import MaterialStore

    store_ = MaterialStore(db_path=tmp_path / "u.db")
    store_.refresh(paths=[mini_xml])

    with pytest.raises(KeyError, match="unknown-id"):
        create_material_file("unknown-id", store_=store_)
```

Run: expected FAIL with ImportError (create_material_file missing).

- [ ] **Step 3: Adapt wrapper creation + implement create_material_file**

In `project_library.py`, change `create_project_library` to take primitives instead of a Materials dataclass (batch caller updated below):

```python
    def create_project_library(self, company: str, companyid: str, ver: int, crd, role: str = "MANUFACTURER"):
        file = self.file

        role_entity = file.createIfcActorRole(role)
        person = file.createIfcPerson(
            Identification=str(uuid.uuid4()),
            FamilyName="Unknown",
            GivenName="Unknown",
            Roles=[role_entity],
        )
        organisation = file.createIfcOrganization(Identification=str(companyid), Name=str(company), Roles=[role_entity])
        person_and_organisation = file.createIfcPersonAndOrganization(person, organisation, Roles=[role_entity])
        owner_history = file.createIfcOwnerHistory(
            OwningUser=person_and_organisation,
            OwningApplication=self.application,
            CreationDate=max(int(utils.date_from_xml(crd).timestamp()), -2147483648),
        )
        self.owner_history = owner_history
        file.createIfcProjectLibrary(
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=owner_history,
            Name=str(company),
            Description=f"Material library converted from materialsdb xml for company {company}",
            ObjectType="MaterialLibrary",
            LongName=f"{company} version {ver}",
        )
```

Update the existing batch caller at module bottom:

```python
def create_project_library_from_xml(xml_path):
    library = ProjectLibrary()
    deserialiser = XmlDeserialiser()
    source = deserialiser.from_xml(str(xml_path))
    library.create_project_library(
        company=source.company, companyid=source.companyid, ver=source.ver, crd=source.crd
    )
    library.create_materials(source)
    return library.file
```

Add to `material_builder.py`:

```python
def create_material_file(material_id, schema="IFC4", store_=None):
    from materialsdb import query

    store_ = store_ or query.get_store()
    summary = store_.get_summary(material_id)
    if summary is None:
        raise KeyError(f"Unknown materialsdb material id: {material_id}")
    material = store_.get(material_id)

    from materialsdb.ifc.project_library import ProjectLibrary

    library = ProjectLibrary(schema=schema)
    library.create_project_library(
        company=summary.company,
        companyid=summary.company_id or "",
        ver=1,  # single-material files carry no source revision
        crd=utils.new_tdatetime(),
    )
    add_material(library.file, material, company_id=summary.company_id or "", company=summary.company)
    return library.file
```

(Lazy imports inside the function avoid an import cycle: query -> store stays independent of ifc, and project_library imports material_builder at module level.)

- [ ] **Step 4: Verify + commit**

All four Global Constraints verification commands green; new tests pass; characterization inventory unchanged EXCEPT the already-recorded identity-pset delta.

```bash
git add src/materialsdb/store.py src/materialsdb/ifc/ tests/test_store.py tests/test_material_builder.py
git commit -m "feat: standalone single-material IFC file via create_material_file"
```

---

### Task 5: Example script + README + final gates

**Files:**
- Create: `examples/create_single_material_ifc.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above
- Produces: documented public entry points per the spec's Docs section.

- [ ] **Step 1: Write the example script**

Create `examples/create_single_material_ifc.py`:

```python
"""Create a standalone IFC file for a single materialsdb material.

Usage:
    python examples/create_single_material_ifc.py <material_id> [output.ifc]
"""
import sys

from materialsdb import config, query
from materialsdb.ifc.material_builder import create_material_file


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    material_id = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else f"{material_id}.ifc"

    query.refresh()  # incremental update from cached producer xml
    config.set_lang("en")
    config.set_country("CH")
    file = create_material_file(material_id)
    file.write(output)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: README section**

Insert after the existing `# Querying materials` section (before `# How to install`), a fenced python block titled:

`# Create a single material in IFC :`

with this prose and code (match the README's existing heading style - `#` level, trailing ` :`):

Append one material into an existing ifcopenshell file (idempotent), or build a minimal standalone file.

```python
from materialsdb import query
from materialsdb.ifc.material_builder import add_material, create_material_file

material = query.get_material("<materialsdb-id>")

add_material(existing_ifc_file, material, company="Producer")   # idempotent append
file = create_material_file("<materialsdb-id>")                 # standalone .ifc
file.write("single_material.ifc")
```

- [ ] **Step 3: Full verification**

Run all four Global Constraints verification commands plus the full suite. Everything green; characterization test still passing with only the recorded identity-pset delta.

- [ ] **Step 4: Commit**

```bash
git add examples/create_single_material_ifc.py README.md
git commit -m "docs: single-material IFC creation example and readme section"
```
