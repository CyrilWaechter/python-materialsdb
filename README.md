python-materialsdb is an unofficial python library for [materialsdb.org][1] open standard.

# Features :
* serialiser.py :
    * from xml : deserialise from materialsdb*.xsd compliant xml file
    * to xml : serealise classes to a materialsdb*.xsd compliant xml file
* classes.py : generated classes
* classes_generator.py : generate classes (dataclasses except for simple type) for materialsdb*.xsd elements
* cache.py : cache latest materials data from producers
* config.py : set and get user config as language and country
* ifc/project_library : convert deserialised source into an IfcProjectLibrary

# Usage :
## Convert latest material data to ifc
```python
from materialsdb import cache
from materialsdb.serialiser import XmlDeserialiser
from materialsdb.ifc.project_library import ProjectLibrary

cache.update_producers_data()
deserialiser = XmlDeserialiser()
for producer in cache.producers():
    source = deserialiser.from_xml(str(producer))
    library = ProjectLibrary()
    library.create_project_library(str(producer))
    library.create_materials(str(producer))
    library.file.write(producer.name.with_suffix(".ifc"))
```

# Dependencies
* [lxml][2] (BSD) : xml parser
* [ifcopenshell][3] (LGPL) : ifc read/write

# Third parties :
* [materialsdb.org][1] (GPL) : materials schema

[1]: http://www.materialsdb.org
[2]: https://lxml.de
[3]: ifcopenshell.org