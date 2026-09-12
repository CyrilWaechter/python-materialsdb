import io
import typing
from pathlib import Path

import pytest
from lxml import etree

pytest.importorskip("lxml")


INDEX_XML = b"""<?xml version="1.0"?>
<companies>
<company id="A" href="https://x.org/a.xml" LastKnownDate="2026-01-01" KnownVersion="1"/>
<company id="B" href="https://x.org/b.xml" LastKnownDate="2026-01-01" KnownVersion="1"/>
</companies>"""

PRODUCER_XML = b"<materials><material id='m'/></materials>"


class _Recorded(typing.TypedDict):
    urls: list[str]
    dests: list[str]
    index: bytes


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    from materialsdb import cache

    cache_dir = tmp_path / "materialsdb"
    monkeypatch.setattr(cache, "get_cache_folder", lambda: cache_dir)
    # single-index scenario: the second real index would otherwise double every download;
    # defaults are bound at def time, so patch __defaults__ too
    single = [cache.MATERIALSDBINDEXURLLIST[0]]
    monkeypatch.setattr(cache, "MATERIALSDBINDEXURLLIST", single)
    monkeypatch.setattr(cache.update_producers_data, "__defaults__", (single, None))
    monkeypatch.setattr(cache.updates_available, "__defaults__", (single,))
    recorded: _Recorded = {"urls": [], "dests": [], "index": INDEX_XML}

    def fake_urlopen(index):
        return io.BytesIO(recorded["index"])

    def fake_urlretrieve(url, dest):
        recorded["urls"].append(url)
        recorded["dests"].append(Path(dest).name)
        Path(dest).write_bytes(PRODUCER_XML)

    monkeypatch.setattr(cache.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cache.urllib.request, "urlretrieve", fake_urlretrieve)
    return cache, cache_dir, recorded


def test_first_refresh_downloads_all_with_global_total(cache_env):
    cache, cache_dir, recorded = cache_env
    progress = []
    report = cache.update_producers_data(
        on_progress=lambda done, total, name: progress.append((done, total, name)) or False
    )

    assert recorded["urls"] == ["https://x.org/a.xml", "https://x.org/b.xml"]
    assert progress == [(1, 2, "a.xml"), (2, 2, "b.xml")]
    assert [p.name for p in report.updated] == ["a.xml", "b.xml"]
    assert report.existing == []
    assert (cache_dir / "Producers" / "a.xml").exists()  # downloads land in Producers dir
    assert (cache_dir / "ProducerIndex.xml").exists()  # new index persisted


def test_second_refresh_uses_existing_files(cache_env):
    cache, _, recorded = cache_env
    report = cache.update_producers_data()
    assert [p.name for p in report.updated] == ["a.xml", "b.xml"]
    recorded["urls"].clear()

    progress = []
    report2 = cache.update_producers_data(
        on_progress=lambda done, total, name: progress.append((done, total, name)) or False
    )

    assert recorded["urls"] == []
    assert progress == []  # nothing to download → no callbacks
    assert [p.name for p in report2.existing] == ["a.xml", "b.xml"]
    assert report2.updated == []


def test_outdated_single_producer_updates_with_total_one(cache_env):
    cache, cache_dir, recorded = cache_env
    cache.update_producers_data()
    recorded["urls"].clear()

    # age producer A in the cached index → next refresh must download only A
    index_path = cache_dir / "ProducerIndex.xml"
    tree = etree.parse(str(index_path))
    for company in tree.getroot():
        if company.get("id") == "A":
            company.set("LastKnownDate", "2025-01-01")
    tree.write(str(index_path))

    progress = []
    record = cache.update_producers_data(
        on_progress=lambda done, total, name: progress.append((done, total, name)) or False
    )

    assert recorded["urls"] == ["https://x.org/a.xml"]
    assert progress == [(1, 1, "a.xml")]
    assert [p.name for p in record.updated] == ["a.xml"]
    assert [p.name for p in record.deleted] == ["a.xml"]  # stale file removed before re-download
    assert [p.name for p in record.existing] == ["b.xml"]


def test_updates_available(cache_env):
    cache, _, recorded = cache_env
    assert cache.updates_available() is True  # empty cache: everything counts as new
    cache.update_producers_data()
    assert cache.updates_available() is False

    # bump the remote index → update available again
    recorded["index"] = INDEX_XML.replace(
        b'<company id="B" href="https://x.org/b.xml" LastKnownDate="2026-01-01" KnownVersion="1"/>',
        b'<company id="B" href="https://x.org/b.xml" LastKnownDate="2026-06-01" KnownVersion="1"/>',
    )

    assert cache.updates_available() is True


def test_cancel_stops_between_downloads(cache_env):
    cache, cache_dir, recorded = cache_env

    def cancel_after_first(done, total, name):
        return done >= 1  # truthy → abort

    report = cache.update_producers_data(on_progress=cancel_after_first)

    assert recorded["urls"] == ["https://x.org/a.xml"]  # second download never starts
    assert [p.name for p in report.updated] == ["a.xml"]
    assert (cache_dir / "Producers" / "b.xml").exists() is False
