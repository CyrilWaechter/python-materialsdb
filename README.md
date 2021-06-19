python-materialsdb is an unofficial python library for [materialsdb.org][1] an open format and database for building materials.

# Features :
## Package
* serialiser.py :
    * from xml : deserialise from materialsdb*.xsd compliant xml file
    * to xml : serealise classes to a materialsdb*.xsd compliant xml file
* classes.py : generated classes corresponding to XML elements
* cache.py : cache latest materials data from producers
* config.py : set and get user config as language and country
* ifc/project_library.py : convert deserialised source into IFC (IfcProjectLibrary)

## devutils
* classes_generator.py : generate classes (dataclasses except for simple type) for materialsdb*.xsd elements

# config
Materials data are often localized. You can set your language and country this way:
```python
from materialsdb import config

config.set_lang("fr")
config.set_country("CH")
```
Note: in materialsdb standard languages are [ISO 639-1](https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes) codes and countries are [ISO_3166-1_alpha-2](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2) codes.

# Usage examples :
Check out some [examples](examples):
* [Convert lastest materials data to ifc](examples/generate_ifc_project_libraries.py)
* [Create your own materialsdb.org compliant XML](examples/create_layers.py)

# How to install
## Using pip
```bash
pip install python-materialsdb
```

# Dependencies
* [lxml][2] (BSD) : xml parser
* [ifcopenshell][3] (LGPL) : ifc read/write

# Third parties :
* [materialsdb.org][1] (GPL) : materials schema

[1]: http://www.materialsdb.org
[2]: https://lxml.de
[3]: ifcopenshell.org