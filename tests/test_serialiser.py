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
