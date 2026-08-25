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
