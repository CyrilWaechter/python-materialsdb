import lxml
from materialsdb import cache
from materialsdb.serialiser import XmlDeserialiser
from materialsdb.ifc.project_library import ProjectLibrary

cache.update_producers_data()
deserialiser = XmlDeserialiser()
for producer in cache.producers():
    source = deserialiser.from_xml(str(producer))
    library = ProjectLibrary()
    library.create_project_library(source)
    library.create_materials(source)
    library.file.write(producer.with_suffix(".ifc").name)
