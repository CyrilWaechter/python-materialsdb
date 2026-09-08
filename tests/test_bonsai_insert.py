import sys
from pathlib import Path

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell

from materialsdb.gui.listener import build_add_materials_payload
from materialsdb.store import MaterialStore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bonsai_addon"))

from insert import (  # ty: ignore[unresolved-import] - add-on module on a side path
    apply_add_construction,
    apply_add_materials,
)


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


@pytest.fixture
def store(tmp_path, mini_xml):
    store_ = MaterialStore(db_path=tmp_path / "insert.db")
    store_.refresh(paths=[mini_xml])
    yield store_
    store_.close()


def _payload(store_):
    payload, missing = build_add_materials_payload(store_, [{"id": "00000000-0000-0000-0000-000000000001"}])
    assert missing == []
    return payload


def test_apply_creates_materials_psets_layers(store):
    file = ifcopenshell.file(schema="IFC4")

    created = apply_add_materials(file, _payload(store))

    assert created == 2
    assert len(file.by_type("IfcMaterial")) == 2
    identity = [p for p in file.by_type("IfcMaterialProperties") if p.Name == "materialsdb"]
    assert len(identity) == 2
    for pset in identity:
        props = {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}
        assert props["material_id"] == "00000000-0000-0000-0000-000000000001"
        assert props["company"] == "Mini SA"
    org_layers = [p for p in file.by_type("IfcMaterialProperties") if p.Name == "materialsdb.org_layer"]
    assert len(org_layers) == 2
    layer_ids = set()
    for pset in org_layers:
        props = {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}
        layer_ids.add(props["layer_id"])
    assert layer_ids == {"00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"}
    layers = sorted(file.by_type("IfcMaterialLayer"), key=lambda l: l.LayerThickness)
    assert [round(l.LayerThickness, 3) for l in layers] == [0.1, 0.2]
    assert {l.Description for l in layers} == {
        "00000000-0000-0000-0000-0000000000a1",
        "00000000-0000-0000-0000-0000000000a2",
    }
    assert len(file.by_type("IfcMaterialLayerSet")) == 2


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


def test_apply_is_idempotent(store):
    file = ifcopenshell.file(schema="IFC4")
    payload = _payload(store)

    assert apply_add_materials(file, payload) == 2
    counts = (len(file.by_type("IfcMaterial")), len(file.by_type("IfcMaterialLayer")))

    assert apply_add_materials(file, payload) == 0
    assert (len(file.by_type("IfcMaterial")), len(file.by_type("IfcMaterialLayer"))) == counts


def test_apply_layer_subset(store):
    file = ifcopenshell.file(schema="IFC4")
    payload, missing = build_add_materials_payload(
        store, [{"id": "00000000-0000-0000-0000-000000000001", "layer_ids": ["00000000-0000-0000-0000-0000000000a1"]}]
    )

    assert missing == []
    assert apply_add_materials(file, payload) == 1
    assert len(file.by_type("IfcMaterialLayer")) == 1


def test_apply_thick_less_layer_is_idempotent():
    """layer: null entries still write a materialsdb.org_layer pset; the
    idempotence key must read the layer_id from that pset so a re-send is a
    no-op instead of a duplicate material."""
    file = ifcopenshell.file(schema="IFC4")
    payload = {
        "materials": [
            {
                "name": "Thick-less material",
                "identity": {"material_id": "00000000-0000-0000-0000-000000000003"},
                "layer": None,
                "psets": {"materialsdb.org_layer": {"layer_id": "00000000-0000-0000-0000-0000000000b1", "thick": 0.0}},
            }
        ]
    }

    assert apply_add_materials(file, payload) == 1
    assert len(file.by_type("IfcMaterial")) == 1
    assert len(file.by_type("IfcMaterialLayer")) == 0

    assert apply_add_materials(file, payload) == 0
    assert len(file.by_type("IfcMaterial")) == 1


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
    payload["construction"].update(overrides)  # ty: ignore[unresolved-attribute]
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

    assert summary["materials_created"] == 1  # Isolant A reused via identity pset; Beton B created
    materials = file.by_type("IfcMaterial")
    assert len(materials) == 3  # 2 from apply_add_materials (Isolant A x2 layers) + Beton B
