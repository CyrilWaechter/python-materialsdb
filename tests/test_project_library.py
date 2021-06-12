from materialsdb.serialiser import XmlDeserialiser
from materialsdb.ifc import project_library


def test_create_project_library():
    file = project_library.create_project_library_from_xml("example_v103.xml")
    file.write("example_v103.ifc")
