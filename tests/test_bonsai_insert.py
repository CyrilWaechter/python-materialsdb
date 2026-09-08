import sys
from pathlib import Path

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell

from materialsdb.gui.listener import build_add_materials_payload
from materialsdb.store import MaterialStore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bonsai_addon"))

from insert import apply_add_materials  # ty: ignore[unresolved-import] - add-on module on a side path


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
