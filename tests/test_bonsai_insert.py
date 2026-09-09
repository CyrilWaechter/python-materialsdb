import sys
from pathlib import Path

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element

from materialsdb.gui.listener import build_add_materials_payload
from materialsdb.store import MaterialStore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bonsai_addon"))

from insert import (  # ty: ignore[unresolved-import] - add-on module on a side path
    apply_add_construction,
    apply_add_materials,
    existing_materials_by_id,
)
from read_construction import (  # ty: ignore[unresolved-import] - add-on module on a side path
    ReadError,
    read_construction_from_element,
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

    assert summary == {"types_created": 1, "sets_updated": 0, "materials_created": 2, "placeholders_matched": 0}
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

    assert summary == {"types_created": 0, "sets_updated": 1, "materials_created": 0, "placeholders_matched": 0}
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


def test_construction_keeps_non_material_associations():
    file = ifcopenshell.file(schema="IFC4")
    # a foreign same-named type carrying a (stub) non-material association
    wall_type = ifcopenshell.api.run("root.create_entity", file, ifc_class="IfcWallType", name="Mur 20+16")
    classification = file.create_entity(
        "IfcClassification", Source=None, Edition=None, EditionDate=None, Name="Walls CS"
    )
    ifcopenshell.api.run(
        "classification.add_reference",
        file,
        products=[wall_type],
        identification="23.11",
        name="Walls",
        classification=classification,
    )
    assert any(a.is_a("IfcRelAssociatesClassification") for a in wall_type.HasAssociations)

    summary = apply_add_construction(file, CONSTRUCTION_PAYLOAD)

    assert summary["types_created"] == 0  # same-named type kept
    assert any(a.is_a("IfcRelAssociatesClassification") for a in wall_type.HasAssociations)
    assert any(a.is_a("IfcRelAssociatesMaterial") for a in wall_type.HasAssociations)


def test_existing_materials_by_id_reads_via_psets(store):
    file = ifcopenshell.file(schema="IFC4")
    apply_add_materials(file, _payload(store))

    found = existing_materials_by_id(file)

    assert set(found) == {"00000000-0000-0000-0000-000000000001"}
    assert found["00000000-0000-0000-0000-000000000001"].Name == "Isolant A"


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
    assert len(file.by_type("IfcMaterial")) == materials_before + summary["materials_created"]  # no duplicate
    layer_set = file.by_type("IfcWallType")[0].HasAssociations[0].RelatingMaterial
    layers = sorted(layer_set.MaterialLayers, key=lambda l: l.LayerThickness)
    assert layers[0].Material.Name == "Brique terrecuite"
    assert layers[1].Material.Name == "Isolant A"


def test_construction_placeholder_created_with_thermal_when_absent():
    file = ifcopenshell.file(schema="IFC4")

    summary = apply_add_construction(file, MIXED_PAYLOAD)

    assert summary["placeholders_matched"] == 0
    assert summary["materials_created"] == 2
    brique = next(m for m in file.by_type("IfcMaterial") if m.Name == "Brique terrecuite")
    thermal = ifcopenshell.util.element.get_psets(brique).get("Pset_MaterialThermal", {})
    assert thermal.get("ThermalConductivity") == 0.21


def _make_type_with_layer_set(file, type_class="IfcWallType", name="Mur 20+16"):
    type_element = ifcopenshell.api.run("root.create_entity", file, ifc_class=type_class, name=name)
    layer_set = ifcopenshell.api.run("material.add_material_set", file, name=name, set_type="IfcMaterialLayerSet")
    ifcopenshell.api.run(
        "material.assign_material", file, products=[type_element], type="IfcMaterialLayerSet", material=layer_set
    )
    return type_element, layer_set


def test_read_construction_from_occurrence():
    file = ifcopenshell.file(schema="IFC4")
    type_element, layer_set = _make_type_with_layer_set(file)
    occurrence = ifcopenshell.api.run("root.create_entity", file, ifc_class="IfcWall", name="W")
    ifcopenshell.api.run("type.assign_type", file, related_objects=[occurrence], relating_type=type_element)
    resolvable = ifcopenshell.api.run("material.add_material", file, name="Isolant A")
    identity = ifcopenshell.api.run("pset.add_pset", file, product=resolvable, name="materialsdb")
    ifcopenshell.api.run(
        "pset.edit_pset", file, pset=identity, properties={"material_id": "00000000-0000-0000-0000-000000000001"}
    )
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

    bare_type = ifcopenshell.api.run("root.create_entity", file, ifc_class="IfcWallType", name="Bare")  # no material
    with _pytest.raises(ReadError, match="type has no material"):
        read_construction_from_element(bare_type)

    type_element2, _ = _make_type_with_layer_set(file, name="T2")
    plain = ifcopenshell.api.run("material.add_material", file, name="M")
    ifcopenshell.api.run("material.assign_material", file, products=[type_element2], type="IfcMaterial", material=plain)
    with _pytest.raises(ReadError, match="material is not an IfcMaterialLayerSet: IfcMaterial"):
        read_construction_from_element(type_element2)


def test_read_construction_materialless_layer_becomes_placeholder():
    file = ifcopenshell.file(schema="IFC4")
    type_element, layer_set = _make_type_with_layer_set(file)
    ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=None)
    # this ifcopenshell defaults add_layer thickness to 0.1; reset to 0 to exercise
    # the missing-thickness contract (reader: `float(thickness) if thickness else 0.0`)
    ifcopenshell.api.run(
        "material.edit_layer", file, layer=layer_set.MaterialLayers[0], attributes={"LayerThickness": 0.0}
    )

    construction = read_construction_from_element(type_element)

    assert construction["layers"][0]["material_id"] is None
    assert construction["layers"][0]["thickness_m"] == 0.0  # LayerThickness 0 counts as missing
    assert construction["layers"][0]["placeholder"] == {"name": "", "lambda_value": None}
