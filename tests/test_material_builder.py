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

    # Pinned golden snapshot of current batch-export behavior. This is a
    # characterization test: it records what the code does today, not what it
    # should do. Update values only when intentional behavior changes.
    # 2026-08-25: +3 IfcMaterialProperties / +9 IfcPropertySingleValue are the
    # new 'materialsdb' identity psets (id/company_id/company IfcText) written
    # per created IfcMaterial by the shared MaterialBuilder (batch passes no
    # verxml). All other counts unchanged vs the pre-refactor snapshot.
    inventory = _entity_inventory(library.file)
    expected_inventory = {
        "IfcAddress": 1,
        "IfcOrganization": 2,  # developer org (application) + manufacturer org
        "IfcApplication": 1,
        "IfcActorRole": 1,
        "IfcPerson": 1,
        "IfcPersonAndOrganization": 1,
        "IfcOwnerHistory": 3,  # 1 explicit + 2 added by assign_material for walls
        "IfcProjectLibrary": 1,
        "IfcRepresentationContext": 1,
        "IfcColourRgb": 3,  # one per material (explicit xml color or category color)
        "IfcSurfaceStyleShading": 3,
        "IfcSurfaceStyle": 3,
        "IfcStyledItem": 3,
        "IfcStyledRepresentation": 3,
        "IfcMaterial": 3,  # one per layer across fixture materials (2 + 1 + 0)
        "IfcPropertySingleValue": 15,
        "IfcMaterialProperties": 9,
        "IfcMaterialLayer": 3,
        "IfcMaterialLayerSet": 3,
        "IfcWallType": 2,  # mat1 declares wall="1" -> one per its 2 layers
        "IfcRelAssociatesMaterial": 2,
    }
    assert inventory == expected_inventory
    material_names = sorted(m.Name for m in library.file.by_type("IfcMaterial"))
    assert material_names == ["Beton B", "Isolant A", "Isolant A"]


import ifcopenshell

from materialsdb.ifc.material_builder import (
    MATERIALSDB_PSET,
    MaterialBuilder,
    add_material,
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

    created = builder.build(material, company_id="A1B85A67", company="Mini SA", verxml=3)

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

    # ifcopenshell returns fresh wrapper objects per access: compare by value, not identity
    assert builder.find_existing(str(material.id)) == created[0]
    assert builder.find_existing("unknown-id") is None


def test_style_cache_bounds_styles(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    builder = MaterialBuilder(file)
    for material in mini_source.material:  # same category/color repeatedly
        for _ in range(3):
            builder.build(material, company="Mini SA")

    # Insulation color + Concrete fallback + Others(no color on mat 3 unused) bounded
    assert len(file.by_type("IfcSurfaceStyle")) <= 3


def test_add_material_is_idempotent(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    material = mini_source.material[0]

    first = add_material(file, material, company_id="A1B85A67", company="Mini SA", verxml=3)
    count_before = len(file.by_type("IfcMaterial"))
    second = add_material(file, material, company_id="A1B85A67", company="Mini SA", verxml=3)

    # ifcopenshell hands out fresh wrapper objects per access: compare by value
    assert first == second
    assert len(file.by_type("IfcMaterial")) == count_before


def test_replace_rebuilds_with_fresh_entity_ids(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    material = mini_source.material[0]

    add_material(file, material, company="Mini SA", verxml=3)
    old_ids = [m.id() for m in file.by_type("IfcMaterial")]

    new = add_material(file, material, company="Mini SA", verxml=3, replace=True)

    assert new is not None and new.id() not in old_ids
    assert _identity_id(file, new) == str(material.id)
    assert len(file.by_type("IfcMaterial")) == len(old_ids)  # same total, rebuilt


def test_purge_keeps_shared_styles(mini_source):
    from materialsdb.ifc.material_builder import purge_material

    file = ifcopenshell.file(schema="IFC4")
    builder = MaterialBuilder(file)
    a = builder.build(mini_source.material[0], company="Mini SA")  # Insulation color style
    b = builder.build(mini_source.material[1], company="Mini SA")  # Concrete category style

    styles_before = len(file.by_type("IfcSurfaceStyle"))
    # Capture ids before purging: wrappers of removed entities dangle in ifcopenshell
    a_id = a[0].id()
    b_id = b[0].id()
    purge_material(file, a[0])

    # IfcMaterial has no GlobalId (not an IfcRoot subtype): identify by STEP id
    remaining = {m.id() for m in file.by_type("IfcMaterial")}
    assert a_id not in remaining
    assert b_id in remaining
    assert len(file.by_type("IfcSurfaceStyle")) == styles_before  # shared styles untouched
