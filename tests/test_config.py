import json

import pytest

from materialsdb import config


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)
    return tmp_path


def test_scheme_defaults_empty_and_roundtrips():
    assert config.get_scheme() == ""
    config.set_scheme("MonPalette")
    assert config.get_scheme() == "MonPalette"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("false", False), (True, True), ("yes", True), ("0", False)],
)
def test_ignore_producer_color_accepts_strings_and_bools(config_dir, raw, expected):
    (config_dir / "config.json").write_text(json.dumps({"lang": "fr", "country": "CH", "ignore_producer_color": raw}))
    assert config.get_ignore_producer_color() is expected


def test_ignore_producer_color_defaults_false_and_roundtrips():
    assert config.get_ignore_producer_color() is False
    config.set_ignore_producer_color(True)
    assert config.get_ignore_producer_color() is True
    assert config.get_base_config()["ignore_producer_color"] == "true"
