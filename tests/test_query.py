import pytest

from materialsdb import query
from materialsdb.store import MaterialStore


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


@pytest.fixture
def isolated_store(tmp_path, monkeypatch, mini_xml):
    query.get_store.cache_clear()
    monkeypatch.setattr(
        "materialsdb.query.MaterialStore",
        lambda: MaterialStore(db_path=tmp_path / "q.db"),
    )
    store = query.get_store()
    store.refresh(paths=[mini_xml])
    yield store
    store.close()
    query.get_store.cache_clear()


def test_get_material(isolated_store):
    material = query.get_material("00000000-0000-0000-0000-000000000002")
    assert material is not None
    assert material.information.group == "Concrete"


def test_search(isolated_store):
    rows = query.search("beton")
    assert len(rows) == 1
