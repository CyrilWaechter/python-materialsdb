import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))
from materialsdb.serialiser import XmlDeserialiser, XmlSerialiser


def test_deserialise_and_serialise():
    xml_path = "example_v103.xml"
    deserialiser = XmlDeserialiser()
    source = deserialiser.from_xml(xml_path)
    serialiser = XmlSerialiser()
    serialiser.to_xml(source, xml_path="test.xml")


def test_mini_fixture_roundtrip(tmp_path, mini_source):
    assert len(mini_source.material) == 3
    assert mini_source.company == "Mini SA"
    out = tmp_path / "roundtrip.xml"
    XmlSerialiser().to_xml(mini_source, xml_path=str(out))
    reparsed = XmlDeserialiser().from_xml(str(out))
    assert len(reparsed.material) == 3


import pytest


@pytest.mark.skipif(
    not Path("example_v103.xml").exists(),
    reason="example_v103.xml not present locally",
)
def test_type_hints_are_cached():
    from materialsdb import serialiser

    serialiser.cached_type_hints.cache_clear()
    deserialiser = XmlDeserialiser()
    deserialiser.from_xml("example_v103.xml")
    hits_after_first = serialiser.cached_type_hints.cache_info().hits
    deserialiser.from_xml("example_v103.xml")
    assert serialiser.cached_type_hints.cache_info().hits > hits_after_first


import shutil


def test_from_xml_files_reports_corrupt_and_parses_rest(tmp_path, mini_xml):
    good_a = tmp_path / "a.xml"
    good_b = tmp_path / "b.xml"
    bad = tmp_path / "bad.xml"
    shutil.copy(mini_xml, good_a)
    shutil.copy(mini_xml, good_b)
    bad.write_text("<materials><unclosed>", encoding="utf-8")

    results = {
        path.name: source
        for path, source in XmlDeserialiser().from_xml_files([good_a, good_b, bad])
    }

    assert results["a.xml"] is not None
    assert len(results["a.xml"].material) == 3
    assert results["b.xml"] is not None
    assert results["bad.xml"] is None
