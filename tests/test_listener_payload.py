import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell

from materialsdb.gui.listener import build_add_materials_payload
from materialsdb.ifc.material_builder import MATERIALSDB_PSET, MaterialBuilder
from materialsdb.store import MaterialStore


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


@pytest.fixture
def store(tmp_path, mini_xml):
    store_ = MaterialStore(db_path=tmp_path / "payload.db")
    store_.refresh(paths=[mini_xml])
    yield store_
    store_.close()


def test_payload_one_entry_per_layer(store):
    payload, missing = build_add_materials_payload(store, [{"id": "00000000-0000-0000-0000-000000000001"}])

    assert missing == []
    assert payload["action"] == "add_materials"
    assert len(payload["materials"]) == 2  # Isolant A has 2 layers
    first = payload["materials"][0]
    assert first["source_id"] == "00000000-0000-0000-0000-000000000001"
    assert first["name"] == "Isolant A"
    assert first["category"] == "Insulation"
    assert first["color"] == 16711680
    assert first["identity"] == {
        "material_id": "00000000-0000-0000-0000-000000000001",
        "company_id": "A1B85A67-5B1E-4960-A297-2DE8275049C5",
        "company": "Mini SA",
    }
    assert first["layer"]["layer_id"] == "00000000-0000-0000-0000-0000000000a1"
    assert first["layer"]["thick_m"] == 0.2  # 200 mm
    assert "materialsdb.org_layer" in first["psets"]


def test_payload_layer_subset(store):
    payload, missing = build_add_materials_payload(
        store, [{"id": "00000000-0000-0000-0000-000000000001", "layer_ids": ["00000000-0000-0000-0000-0000000000a2"]}]
    )

    assert missing == []
    assert len(payload["materials"]) == 1
    assert payload["materials"][0]["layer"]["layer_id"] == "00000000-0000-0000-0000-0000000000a2"
    assert payload["materials"][0]["layer"]["thick_m"] == 0.1


def test_payload_missing_and_layerless_ids(store):
    payload, missing = build_add_materials_payload(
        store,
        [{"id": "nope"}, {"id": "00000000-0000-0000-0000-000000000003"}],  # unknown + material 3 has no layers
    )

    assert payload["materials"] == []
    assert missing == ["nope", "00000000-0000-0000-0000-000000000003"]


def _builder_entity_map(file):
    """IfcMaterial STEP id -> {pset_name: {prop: value}} for its psets."""
    result = {}
    for pset in file.by_type("IfcMaterialProperties"):
        materials = pset.Material if isinstance(pset.Material, (list, tuple)) else [pset.Material]
        props = {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}
        for material in materials:
            result.setdefault(material.id(), {})[pset.Name] = props
    return result


def test_payload_matches_builder_output(store, mini_source):
    """Equivalence gate: resolver psets (names + values incl. unit factors) are
    exactly what MaterialBuilder writes for the same material."""
    material_id = "00000000-0000-0000-0000-000000000001"
    file = ifcopenshell.file(schema="IFC4")
    MaterialBuilder(file).build(
        mini_source.material[0],
        company_id="A1B85A67-5B1E-4960-A297-2DE8275049C5",
        company="Mini SA",
    )
    built = _builder_entity_map(file)

    payload, missing = build_add_materials_payload(store, [{"id": material_id}])
    assert missing == []
    assert len(payload["materials"]) == 2

    for entry in payload["materials"]:
        matches = [
            psets
            for psets in built.values()
            if psets.get(MATERIALSDB_PSET, {}).get("material_id") == material_id
            and psets.get("materialsdb.org_layer", {}).get("layer_id") == entry["layer_id"]
        ]
        assert len(matches) == 1
        # identity pset travels as the dedicated `identity` field, not in psets
        assert {k: v for k, v in matches[0].items() if k != MATERIALSDB_PSET} == entry["psets"]


def test_build_add_construction_types_mapping(store):
    from materialsdb.gui.listener import build_add_construction_payload

    base = {"layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}]}
    payload, problems = build_add_construction_payload(
        store, body={**base, "name": "wall one", "design_usage": "consDesignForWall"}
    )
    assert problems == []
    assert payload["construction"]["types"] == ["IfcWallType"]
    payload, problems = build_add_construction_payload(
        store, body={**base, "name": "floor one", "design_usage": "consDesignForFloor"}
    )
    assert payload["construction"]["types"] == ["IfcSlabType"]
    payload, problems = build_add_construction_payload(
        store, body={**base, "name": "roof one", "design_usage": "consDesignForRoof"}
    )
    assert payload["construction"]["types"] == ["IfcRoofType"]
    payload, problems = build_add_construction_payload(
        store, body={**base, "name": "generic one", "design_usage": None}
    )
    assert payload["construction"]["types"] == ["IfcWallType", "IfcSlabType", "IfcRoofType"]
    payload, problems = build_add_construction_payload(
        store, body={**base, "name": "odd one", "design_usage": "garbage"}
    )
    assert payload["construction"]["types"] == ["IfcWallType", "IfcSlabType", "IfcRoofType"]


def test_build_add_construction_payload_shape(store):
    from materialsdb.gui.listener import build_add_construction_payload

    payload, problems = build_add_construction_payload(
        store,
        body={
            "name": "Mur 20+16",
            "design_usage": "consDesignForWall",
            "layers": [
                {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.22},  # custom thickness
                {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            ],
        },
    )

    assert problems == []
    construction = payload["construction"]
    assert construction["name"] == "Mur 20+16"
    assert construction["design_usage"] == "consDesignForWall"
    assert [layer["thickness_m"] for layer in construction["layers"]] == [
        0.22,
        0.15,
    ]  # construction thickness, not manufacturer
    first = construction["layers"][0]["material"]
    assert first["name"] == "Isolant A"
    assert first["category"] == "Insulation"
    assert first["identity"] == {
        "material_id": "00000000-0000-0000-0000-000000000001",
        "company_id": "A1B85A67-5B1E-4960-A297-2DE8275049C5",
        "company": "Mini SA",
    }
    assert "psets" not in first  # minimal to_ifc_layer_set-style material


def test_build_add_construction_reports_problems(store):
    from materialsdb.gui.listener import build_add_construction_payload

    payload, problems = build_add_construction_payload(store, body={"name": "", "layers": []})

    assert payload is None
    assert "name required" in problems
    assert "at least one layer required" in problems


def test_build_add_construction_unknown_material(store):
    from materialsdb.gui.listener import build_add_construction_payload

    payload, problems = build_add_construction_payload(
        store, body={"name": "x", "layers": [{"material_id": "nope", "thickness_m": 0.1}]}
    )

    assert payload is None
    assert any("nope" in problem for problem in problems)


def test_build_add_construction_placeholder_passthrough(store):
    from materialsdb.gui.listener import build_add_construction_payload

    payload, problems = build_add_construction_payload(
        store,
        body={
            "name": "Mixed wall",
            "design_usage": "consDesignForWall",
            "layers": [
                {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2},
                {"material_id": None, "thickness_m": 0.18, "placeholder": {"name": "Brique", "lambda_value": 0.21}},
            ],
        },
    )

    assert problems == []
    layers = payload["construction"]["layers"]
    assert layers[0]["material_id"] == "00000000-0000-0000-0000-000000000001"
    assert "placeholder" not in layers[0]
    assert layers[1] == {
        "material_id": None,
        "thickness_m": 0.18,
        "placeholder": {"name": "Brique", "lambda_value": 0.21},
    }
