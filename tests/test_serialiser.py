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
