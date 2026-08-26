import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell

from materialsdb.construction import Construction, ConstructionLayer, to_ifc_layer_set
from materialsdb.ifc.material_builder import MaterialBuilder


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
            ConstructionLayer("00000000-0000-0000-0000-000000000001", thickness_m=0.2),  # Isolant A CH lambda .036
        ],
    )


def test_build_with_layers_false_creates_material_only(mini_source):
    file = ifcopenshell.file(schema="IFC4")
    builder = MaterialBuilder(file)

    created = builder.build(mini_source.material[0], company="Mini SA", with_layers=False)

    assert len(created) == 1
    assert file.by_type("IfcMaterialLayer") == []
    assert file.by_type("IfcMaterialLayerSet") == []
    identity = [p for p in file.by_type("IfcMaterialProperties") if p.Name == "materialsdb"]
    assert len(identity) == 1


def test_to_ifc_layer_set_roundtrip(store, tmp_path):
    construction = make_construction()
    file = to_ifc_layer_set(construction, store)

    out = tmp_path / "construction.ifc"
    file.write(str(out))
    reopened = ifcopenshell.open(str(out))

    layers = sorted(reopened.by_type("IfcMaterialLayer"), key=lambda l: l.LayerThickness)
    assert [round(l.LayerThickness, 3) for l in layers] == [0.15, 0.2]
    assert {l.Description for l in layers} == {
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000001",
    }
    layer_sets = reopened.by_type("IfcMaterialLayerSet")
    assert len(layer_sets) == 1 and layer_sets[0].LayerSetName == "Test wall"
    assert [l.Description for l in layer_sets[0].MaterialLayers] == [
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000001",
    ]
    # identity psets ride along on referenced materials
    identity = [p for p in reopened.by_type("IfcMaterialProperties") if p.Name == "materialsdb"]
    assert len(identity) == 2


def test_to_ifc_rejects_unknown_materials(store):
    bad = Construction(
        name="bad",
        design_usage=None,
        layers=[ConstructionLayer("ffffffff-0000-0000-0000-000000000000", thickness_m=0.1)],
    )
    with pytest.raises(ValueError, match="ffffffff"):
        to_ifc_layer_set(bad, store)
