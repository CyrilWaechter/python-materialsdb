python-materialsdb is python api for [materialsdb.org][1]

# Features :
* serialiser.py :
    * from xml : deserialise from materialsdb*.xsd compliant xml file
    * to xml : serealise classes to a materialsdb*.xsd compliant xml file
* classes.py : generated classes
* classes_generator.py : generate classes (dataclasses except for simple type) for materialsdb*.xsd elements

# Dependencies
* [lxml][2] (BSD) : xml parser

# Third parties :
* [materialsdb.org][1] (GPL) : materials schema

[1]: http://www.materialsdb.org
[2]: https://lxml.de