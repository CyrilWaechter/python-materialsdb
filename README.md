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
* gui/server.py : stdlib-only web application to browse and export cached materials (materialsdb-gui)

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
* [Convert latest materials data to ifc](examples/generate_ifc_project_libraries.py)
* [Create your own materialsdb.org compliant XML](examples/create_layers.py)

# Querying materials :
The library keeps an sqlite index of the cached materials data for fast
filtering and single-material access:

```python
from materialsdb import query

query.refresh()                                   # incremental update from cached xml
rows = query.search("isolant", sort="lambda")     # filtered, sorted summaries
material = query.get_material(rows[0].id)         # full material dataclass
```

# Create a single material in IFC :
Append one material into an existing ifcopenshell file (idempotent), or build a minimal standalone file.

```python
from materialsdb import query
from materialsdb.ifc.material_builder import add_material, create_material_file

material = query.get_material("<materialsdb-id>")

add_material(existing_ifc_file, material, company="Producer")   # idempotent append
file = create_material_file("<materialsdb-id>")                 # standalone .ifc
file.write("single_material.ifc")
```

# Material picker GUI :
Launch the local web application (stdlib only, no extra dependencies):

```bash
materialsdb-gui            # opens http://127.0.0.1:8619 in your browser
```

Or run straight from a source checkout without installing:

```bash
PYTHONPATH=src python3 -m materialsdb.gui
```

Browse, sort and filter all cached materials; multi-select then either export
a standalone `.ifc`, or open one of your own `.ifc` files and append the
selected materials into it. The same HTTP API powers future BIM software
plugins (all mutating calls require a per-launch token).

# How to install
## Using pip
```bash
pip install python-materialsdb
```

# Dependencies
* [lxml][2] (BSD) : xml parser (tested with version 6.1.1)
* [ifcopenshell][3] (LGPL) : ifc read/write (tested with version 0.8.5)

# Third parties :
* [materialsdb.org][1] (GPL) : materials schema

[1]: http://www.materialsdb.org
[2]: https://lxml.de
[3]: ifcopenshell.org