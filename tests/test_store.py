import pytest

from materialsdb.store import MaterialStore


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


@pytest.fixture
def store(tmp_path, mini_xml):
    s = MaterialStore(db_path=tmp_path / "test.db")
    s.refresh(paths=[mini_xml])
    yield s
    s.close()


def test_refresh_populates_all_materials(store):
    assert len(store.summaries()) == 3


def test_get_returns_full_material_equivalent_to_direct_parse(store, mini_source):
    material = store.get("00000000-0000-0000-0000-000000000001")
    original = mini_source.material[0]
    assert str(material.information.names.name[0]) == str(original.information.names.name[0])
    assert material.information.group == "Insulation"
    assert material.layers.layer[0].thermal[0].lambda_value == 0.036


def test_get_unknown_id_returns_none(store):
    assert store.get("nope") is None


def test_get_summary_returns_row_summary(store):
    s = store.get_summary("00000000-0000-0000-0000-000000000001")

    assert s is not None
    assert s.id == "00000000-0000-0000-0000-000000000001"
    assert s.company == "Mini SA"
    assert s.company_id == "A1B85A67-5B1E-4960-A297-2DE8275049C5"
    assert store.get_summary("missing") is None


def test_filters(store):
    insulation = store.summaries(category="Insulation")
    assert len(insulation) == 1
    assert insulation[0].id.endswith("001")

    assert len(store.summaries(company="Mini SA")) == 3

    low_lambda = store.summaries(max_lambda=0.1)
    assert {s.id[-3:] for s in low_lambda} == {"001"}

    walls = store.summaries(usage="wall")
    assert len(walls) == 1


def test_sort_order_and_nulls_last(store):
    rows = store.summaries(sort="lambda")
    assert [r.lambda_min for r in rows] == [0.036, 0.21, None]

    rows = store.summaries(sort="lambda", ascending=False)
    assert [r.lambda_min for r in rows] == [0.21, 0.036, None]

    rows = store.summaries(sort="thick", ascending=False)
    assert [r.thick_min for r in rows] == [150, 100, None]


def test_text_filter_uses_configured_lang(monkeypatch, store):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    rows = store.summaries(text="isol")
    assert len(rows) == 1
    assert rows[0].id.endswith("001")


def test_refresh_is_incremental(store, mini_xml):
    report = store.refresh(paths=[mini_xml])
    assert report.updated == []
    assert len(report.existing) == 1


def test_refresh_rebuilds_changed_file(store, mini_xml, tmp_path):
    copy = tmp_path / "changed.xml"
    copy.write_text(
        mini_xml.read_text(encoding="utf-8").replace("0.036", "0.03"),
        encoding="utf-8",
    )

    report = store.refresh(paths=[copy])
    assert copy in report.updated
    rows = store.summaries(min_lambda=0.03, max_lambda=0.1)
    assert len(rows) == 1
    assert rows[0].lambda_min == 0.03


def test_deleted_producer_rows_are_removed(store):
    store.refresh(paths=[])  # declare current file set empty
    assert store.summaries() == []


def test_corrupt_producer_is_skipped(store, tmp_path, mini_xml):
    bad = tmp_path / "bad.xml"
    bad.write_text("<materials>", encoding="utf-8")
    report = store.refresh(force=True, paths=[mini_xml, bad])
    assert len(report.existing) + len(report.updated) == 1
    assert report.skipped == [bad]
    assert len(store.summaries()) == 3


def test_nonexistent_producer_is_skipped_without_raising(store, tmp_path, mini_xml):
    ghost = tmp_path / "ghost.xml"
    report = store.refresh(paths=[mini_xml, ghost])
    assert report.existing == [mini_xml]
    assert report.updated == []
    assert len(store.summaries()) == 3


@pytest.fixture
def mixed_store(tmp_path, mixed_xml):
    s = MaterialStore(db_path=tmp_path / "mixed.db")
    s.refresh(paths=[mixed_xml])
    yield s
    s.close()


def test_mixed_fixture_rows_and_types(mixed_store):
    rows = {r.id[-3:]: r for r in mixed_store.summaries(sort="id")}
    assert rows["004"].type == "btk"
    assert rows["005"].type == "construction"
    assert rows["004"].thick_min == 200


def test_type_filter(mixed_store):
    assert [r.id[-3:] for r in mixed_store.summaries(type="btk")] == ["004"]
    assert mixed_store.summaries(type="construction")[0].id.endswith("005")
    assert mixed_store.summaries(type="simple") == []
