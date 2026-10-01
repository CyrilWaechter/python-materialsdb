import pytest
from pytest import approx

pytest.importorskip("ifcopenshell")

import ifcopenshell

from materialsdb.gui.listener import build_add_construction_payload, build_add_materials_payload
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
        "fingerprint": store.material_fingerprint("00000000-0000-0000-0000-000000000001"),
        "fingerprint_scheme": "materialsdb-fp/1",
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
        "fingerprint": store.material_fingerprint("00000000-0000-0000-0000-000000000001"),
        "fingerprint_scheme": "materialsdb-fp/1",
    }
    assert "psets" in first  # resolved psets now travel with the material


def test_construction_payload_carries_resolved_psets(store):
    payload, problems = build_add_construction_payload(
        store,
        body={"name": "w", "layers": [{"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2}]},
    )
    assert problems == []
    material = payload["construction"]["layers"][0]["material"]
    org = material["psets"]["materialsdb.org_layer"]
    assert org["layer_id"] == "00000000-0000-0000-0000-0000000000a1"  # the 200 mm layer
    assert org["thick"] == 0.2
    assert payload["construction"]["warnings"] == []


def test_construction_payload_warns_when_thickness_unmatched(store):
    # Isolant A has 200 mm and 100 mm layers; 0.15 m matches neither, and it has
    # more than one layer -> best effort first layer + a warning.
    payload, problems = build_add_construction_payload(
        store,
        body={"name": "w", "layers": [{"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.15}]},
    )
    assert problems == []
    assert payload["construction"]["warnings"]
    material = payload["construction"]["layers"][0]["material"]
    assert "materialsdb.org_layer" in material["psets"]


def test_construction_warning_uses_payload_lang(store):
    """The mismatch warning names the material in the payload's language, not
    the global config language (config is pinned to fr by the fixture)."""
    names = {"fr": "Isolant A", "de": "Daemmstoff A", "": "Material A"}

    def fake_name(material, lang):
        return names.get(lang or "", names[""])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("materialsdb.utils.get_material_name", fake_name)
        payload, problems = build_add_construction_payload(
            store,
            body={
                "name": "w",
                "layers": [{"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.15}],
            },
            lang="de",
        )

    assert problems == []
    material = payload["construction"]["layers"][0]["material"]
    assert material["name"] == "Daemmstoff A"
    assert payload["construction"]["warnings"] == ["Daemmstoff A: design thickness 150 mm matches no layer"]


def test_construction_resolves_noisy_design_thickness(store):
    """IFC lengths carry float noise (a 200 mm layer reads as 0.20000000298 in
    metres); the shared micrometre tolerance must still match the store layer."""
    payload, problems = build_add_construction_payload(
        store,
        body={
            "name": "w",
            "layers": [
                {
                    "material_id": "00000000-0000-0000-0000-000000000001",
                    "thickness_m": 0.200000002980232,
                }
            ],
        },
    )

    assert problems == []
    assert payload["construction"]["warnings"] == []
    org = payload["construction"]["layers"][0]["material"]["psets"]["materialsdb.org_layer"]
    assert org["layer_id"] == "00000000-0000-0000-0000-0000000000a1"  # the 200 mm layer


def test_construction_zero_layer_material_warns_and_has_no_psets(store):
    # material 3 (Sans donnees C) has no layers at all
    payload, problems = build_add_construction_payload(
        store,
        body={"name": "w", "layers": [{"material_id": "00000000-0000-0000-0000-000000000003", "thickness_m": 0.1}]},
    )

    assert problems == []
    material = payload["construction"]["layers"][0]["material"]
    assert "psets" not in material  # no crash, no invented psets
    assert payload["construction"]["warnings"] == ["Sans donnees C: material has no layer"]


def test_construction_single_layer_fallback_has_psets_and_no_warning(store):
    # Beton B has exactly one layer (150 mm); a 300 mm design thickness still
    # resolves to it without a warning (single-layer fallback).
    payload, problems = build_add_construction_payload(
        store,
        body={"name": "w", "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.3}]},
    )

    assert problems == []
    material = payload["construction"]["layers"][0]["material"]
    assert material["psets"]["materialsdb.org_layer"]["layer_id"] == "00000000-0000-0000-0000-0000000000b1"
    assert payload["construction"]["warnings"] == []


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


def _construction_body(design_usage=None, layers=None):
    return {
        "name": "Test wall",
        "design_usage": design_usage,
        "layers": layers
        or [
            {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2},
        ],
    }


def test_construction_payload_generic_has_per_type_u_values(store):
    payload, problems = build_add_construction_payload(store, _construction_body())

    assert problems == []
    u_values = payload["construction"]["u_values"]
    assert sorted(u_values) == ["IfcRoofType", "IfcSlabType", "IfcWallType"]
    assert u_values["IfcWallType"] == approx(u_values["IfcWallType"])
    # fewer inner resistances → higher U: floor(Rsi .17) < wall(.13) < roof(.10)
    assert u_values["IfcSlabType"] < u_values["IfcWallType"] < u_values["IfcRoofType"]


def test_construction_payload_explicit_usage_has_one_u_value(store):
    payload, problems = build_add_construction_payload(store, _construction_body(design_usage="consDesignForWall"))

    assert problems == []
    assert payload["construction"]["u_values"] == {
        "IfcWallType": approx(payload["construction"]["u_values"]["IfcWallType"])
    }
    assert list(payload["construction"]["u_values"]) == ["IfcWallType"]


def _construction_body_with_missing_lambda():
    return {
        "name": "with btk",
        "design_usage": None,
        "layers": [{"material_id": "00000000-0000-0000-0000-000000000004", "thickness_m": 0.3}],
    }


def test_construction_payload_no_u_values_when_lambda_unresolvable(store):
    # mini_producer.xml has no material ...004: rejected as unknown, no u_values computed
    payload, problems = build_add_construction_payload(store, _construction_body_with_missing_lambda())

    assert payload is None
    assert problems == ["unknown material id: 00000000-0000-0000-0000-000000000004"]


def test_payload_carries_fingerprint_and_default_skip(store):
    payload, _ = build_add_materials_payload(store, [{"id": "00000000-0000-0000-0000-000000000001"}])
    first = payload["materials"][0]
    assert first["mode"] == "skip" and first["replaces"] is None and first["update"] is None
    assert first["identity"]["fingerprint_scheme"] == "materialsdb-fp/1"
    assert first["identity"]["fingerprint"]


def test_payload_carries_update_mapping(store):
    payload, _ = build_add_materials_payload(
        store,
        [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "mode": "update",
                "update": {"00000000-0000-0000-0000-0000000000a1": "00000000-0000-0000-0000-0000000000a1"},
            }
        ],
    )
    assert payload["materials"][0]["mode"] == "update"
    assert payload["materials"][0]["update"]


def test_payload_carries_replaces(store):
    replaces = {
        "material_id": "00000000-0000-0000-0000-000000000009",
        "layer_id": "00000000-0000-0000-0000-0000000000c1",
    }
    payload, _ = build_add_materials_payload(
        store, [{"id": "00000000-0000-0000-0000-000000000001", "mode": "replace", "replaces": replaces}]
    )
    first = payload["materials"][0]
    assert first["mode"] == "replace"
    assert first["replaces"] == replaces


def test_construction_payload_carries_fingerprint_mode_and_update(store):
    update = {"00000000-0000-0000-0000-0000000000c1": "00000000-0000-0000-0000-0000000000b1"}
    payload, problems = build_add_construction_payload(
        store,
        body={
            "name": "w",
            "layers": [
                {
                    "material_id": "00000000-0000-0000-0000-000000000002",
                    "thickness_m": 0.15,
                    "mode": "update",
                    "update": update,
                }
            ],
        },
    )
    assert problems == []
    material = payload["construction"]["layers"][0]["material"]
    assert material["mode"] == "update"
    assert material["update"] == update
    assert material["replaces"] is None
    assert material["identity"]["fingerprint_scheme"] == "materialsdb-fp/1"
    assert material["identity"]["fingerprint"]


def test_construction_decision_fields_match_layer_not_index(store):
    """`validate_construction` can drop malformed raw layers, so the validated
    index must not address the raw body: decisions are matched on
    (material_id, thickness_m)."""
    from materialsdb.gui.listener import _raw_decision_layer

    update = {"00000000-0000-0000-0000-0000000000c1": "00000000-0000-0000-0000-0000000000b1"}
    raw_layers = [
        {"material_id": None, "thickness_m": 0.07},  # dropped by validation
        {
            "material_id": "00000000-0000-0000-0000-000000000002",
            "thickness_m": 0.15,
            "mode": "update",
            "update": update,
        },
    ]
    # the validated 0.15 layer is at raw index 1, not 0: positional lookup
    # would hand the dropped layer's (empty) decision to it
    matched = _raw_decision_layer(raw_layers, "00000000-0000-0000-0000-000000000002", 0.15)
    assert matched is raw_layers[1]  # not the argued index 0
    assert matched["mode"] == "update"
    assert matched["update"] == update

    # no unambiguous pair -> omit the decision fields (skip), never guess
    assert _raw_decision_layer(raw_layers, "00000000-0000-0000-0000-000000000002", 0.22) == {}
    assert _raw_decision_layer(raw_layers, "nope", 0.15) == {}
    duplicate = [dict(raw_layers[1]), dict(raw_layers[1])]
    assert _raw_decision_layer(duplicate, "00000000-0000-0000-0000-000000000002", 0.15) == {}

    # IFC float noise on the validated thickness still finds its raw layer
    noisy = [{"material_id": "X", "thickness_m": 0.2, "mode": "update"}]
    assert _raw_decision_layer(noisy, "X", 0.200000002980232)["mode"] == "update"


def test_construction_payload_uses_own_decision_per_layer(store):
    """Each validated layer keeps its own mode/update, resolved by pair."""
    update = {"00000000-0000-0000-0000-0000000000c1": "00000000-0000-0000-0000-0000000000b1"}
    payload, problems = build_add_construction_payload(
        store,
        body={
            "name": "w",
            "layers": [
                {
                    "material_id": "00000000-0000-0000-0000-000000000001",
                    "thickness_m": 0.1,
                    "mode": "replace",
                    "replaces": {"material_id": "00000000-0000-0000-0000-000000000009"},
                },
                {
                    "material_id": "00000000-0000-0000-0000-000000000002",
                    "thickness_m": 0.15,
                    "mode": "update",
                    "update": update,
                },
            ],
        },
    )
    assert problems == []
    first, second = (layer["material"] for layer in payload["construction"]["layers"])
    assert first["mode"] == "replace"
    assert first["replaces"] == {"material_id": "00000000-0000-0000-0000-000000000009"}
    assert first["update"] is None
    assert second["mode"] == "update"
    assert second["update"] == update
    assert second["replaces"] is None


def test_construction_decision_fields_omitted_when_ambiguous(store):
    """Two raw layers with the same (material_id, thickness) cannot be told
    apart: omit the decision fields (skip) rather than guess."""
    update = {"00000000-0000-0000-0000-0000000000c1": "00000000-0000-0000-0000-0000000000b1"}
    layer = {
        "material_id": "00000000-0000-0000-0000-000000000002",
        "thickness_m": 0.15,
        "mode": "update",
        "update": update,
    }
    payload, problems = build_add_construction_payload(
        store,
        body={"name": "w", "layers": [layer, dict(layer)]},
    )
    assert problems == []
    for entry in payload["construction"]["layers"]:
        material = entry["material"]
        assert material["mode"] == "skip"
        assert material["update"] is None
        assert material["replaces"] is None


def test_payload_carries_resolved_producer_style(store):
    payload, missing = build_add_materials_payload(store, [{"id": "00000000-0000-0000-0000-000000000001"}])

    assert missing == []
    first = payload["materials"][0]
    assert first["style_name"] == "color 16711680"  # producer colour wins
    assert first["style_color"] == [1.0, 0.0, 0.0]  # normalised 0xFF0000


def test_construction_payload_carries_category_scheme_style(store):
    from materialsdb.ifc.material_builder import CATEGORIES

    payload, problems = build_add_construction_payload(
        store,
        body={
            "name": "wall",
            "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}],
        },
    )

    assert problems == []
    material = payload["construction"]["layers"][0]["material"]
    assert material["style_name"] == "category Concrete"  # no producer colour -> scheme
    assert material["style_color"] == [round(component / 255, 6) for component in CATEGORIES["Concrete"]["color"]]


def test_payload_honours_custom_scheme_and_override(store, tmp_path, monkeypatch):
    """A custom scheme plus the producer-colour override change the resolved style."""
    from materialsdb import schemes

    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)
    palette = {name: dict(style) for name, style in schemes.CATEGORIES.items()}
    palette["Insulation"] = {"hatch": "", "color": [1, 2, 3]}
    schemes.save("Mine", palette)
    monkeypatch.setattr("materialsdb.config.get_scheme", lambda: "Mine")
    monkeypatch.setattr("materialsdb.config.get_ignore_producer_color", lambda: True)

    payload, problems = build_add_materials_payload(store, [{"id": "00000000-0000-0000-0000-000000000001"}])

    assert problems == []
    first = payload["materials"][0]
    assert first["style_name"] == "category Insulation"  # producer colour 0xFF0000 ignored
    assert first["style_color"] == [round(c / 255, 6) for c in (1, 2, 3)]


def test_construction_payload_honours_override(store, monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_ignore_producer_color", lambda: True)
    payload, problems = build_add_construction_payload(
        store,
        body={"name": "w", "layers": [{"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2}]},
    )
    assert problems == []
    assert payload["construction"]["layers"][0]["material"]["style_name"] == "category Insulation"
