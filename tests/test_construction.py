import pathlib

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


_MINI_XML_PATH = pathlib.Path(__file__).parent / "fixtures" / "mini_producer.xml"


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
    assert roof.u is not None and wall.u is not None and floor.u is not None
    assert roof.u > wall.u > floor.u  # smaller Rsi -> larger U


def test_missing_lambda_layers_flagged_and_excluded(store, mixed_xml):
    # combined refresh: passing a SUBSET would delete previously indexed rows
    store.refresh(paths=[_MINI_XML_PATH, mixed_xml])
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


pytest.importorskip("ifcopenshell")

import ifcopenshell

from materialsdb.ifc.material_builder import MaterialBuilder


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
    import ifcopenshell

    from materialsdb.construction import to_ifc_layer_set

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
    from materialsdb.construction import to_ifc_layer_set

    bad = Construction(
        name="bad",
        design_usage=None,
        layers=[ConstructionLayer("ffffffff-0000-0000-0000-000000000000", thickness_m=0.1)],
    )
    with pytest.raises(ValueError, match="ffffffff"):
        to_ifc_layer_set(bad, store)


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
    _, problems = cm.validate_construction(
        {
            "name": "x",
            "design_usage": None,
            "layers": [{"material_id": "ffffffff-0000-0000-0000-000000000000", "thickness_m": 0.2}],
        },
        store,
    )
    assert problems and "ffffffff" in problems[0]

    _, problems = cm.validate_construction(
        {
            "name": "x",
            "design_usage": None,
            "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": -1}],
        },
        store,
    )
    assert any("thickness" in problem.lower() for problem in problems)
