import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))
from materialsdb.serialiser import XmlDeserialiser
from materialsdb.ifc.project_library import ProjectLibrary


def test_create_project_library():
    xml_path = "example_v103.xml"
    deserialiser = XmlDeserialiser()
    source = deserialiser.from_xml(xml_path)
    library = ProjectLibrary()
    library.create_project_library(source)
    library.create_materials(source)
    library.file.write("example_v103.ifc")
