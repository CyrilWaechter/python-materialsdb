import json

import pytest

from materialsdb import schemes


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)
    return tmp_path


def test_save_get_available_roundtrip(config_dir):
    assert schemes.available() == {schemes.DEFAULT_SCHEME: schemes.CATEGORIES}
    palette = {name: dict(style) for name, style in schemes.CATEGORIES.items()}
    palette["Concrete"] = {"hatch": "", "color": [1, 2, 3]}
    schemes.save("Mon Paletté", palette)
    mon_palette = schemes.get("Mon Paletté")
    assert mon_palette is not None
    assert mon_palette["Concrete"]["color"] == [1, 2, 3]
    assert list(schemes.available()) == ["Lesosai", "Mon Paletté"]
    assert json.loads((config_dir / "schemes.json").read_text())["version"] == 1


@pytest.mark.parametrize("name", ["Lesosai", "import", "", "a/b", "x" * 65])
def test_reserved_or_invalid_names_are_rejected(name):
    with pytest.raises(schemes.SchemeError):
        schemes.save(name, schemes.CATEGORIES)


@pytest.mark.parametrize(
    "categories",
    [
        {k: v for k, v in schemes.CATEGORIES.items() if k != "Concrete"},  # missing category
        {**schemes.CATEGORIES, "Nope": {"hatch": "", "color": [0, 0, 0]}},  # unknown category
        {**schemes.CATEGORIES, "Concrete": {"hatch": "", "color": [0, 0]}},  # wrong length
        {**schemes.CATEGORIES, "Concrete": {"hatch": "", "color": [0, 0, 256]}},  # out of range
        {**schemes.CATEGORIES, "Concrete": {"hatch": "", "color": [0, 0, "x"]}},  # wrong type
    ],
)
def test_validate_rejects_bad_palettes(categories):
    with pytest.raises(schemes.SchemeError):
        schemes.save("Bad", categories)


def test_rename_and_delete_custom_only():
    schemes.save("Old", schemes.CATEGORIES)
    schemes.rename("Old", "New")
    assert schemes.get("Old") is None and schemes.get("New") is not None
    schemes.delete("New")
    assert schemes.get("New") is None
    with pytest.raises(schemes.SchemeError):
        schemes.delete("Lesosai")


def test_corrupt_file_falls_back_to_builtins_with_error(config_dir):
    (config_dir / "schemes.json").write_text("{not json")
    assert schemes.available() == {schemes.DEFAULT_SCHEME: schemes.CATEGORIES}
    assert schemes.load_error()


def test_export_import_envelope_roundtrip():
    schemes.save("Mon Paletté", schemes.CATEGORIES)
    data = schemes.export_payload("Mon Paletté")
    assert data["format"] == schemes.FORMAT and data["name"] == "Mon Paletté"
    schemes.delete("Mon Paletté")
    assert schemes.import_payload(data) == "Mon Paletté"
    with pytest.raises(schemes.SchemeError):
        schemes.import_payload(data)  # existing name
    with pytest.raises(schemes.SchemeError):
        schemes.import_payload({"format": "other", "name": "X", "categories": schemes.CATEGORIES})


def test_material_builder_reexports_are_unchanged():
    from materialsdb.ifc import material_builder as mb

    assert mb.CATEGORIES is schemes.CATEGORIES
    assert mb.SCHEMES == schemes.BUILTIN
    assert mb.DEFAULT_SCHEME == "Lesosai"
