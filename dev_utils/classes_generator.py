# coding=UTF-8
"""This module generate classes from the xml schema definition (.xsd)

© All rights reserved.
ECOLE POLYTECHNIQUE FEDERALE DE LAUSANNE, Switzerland, Laboratory CNPA, 2019-2020

See the LICENSE.md file for more details.

Author : Cyril Waechter
"""
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any
from lxml import etree
from lxml import objectify


@dataclass
class PyAttr:
    with_default: List[str] = field(default_factory=list)
    without_default: List[str] = field(default_factory=list)
    xml_attributes: List[str] = field(default_factory=list)
    xml_elements: List[str] = field(default_factory=list)

    def __str__(self):
        return "\n    ".join(self.without_default + self.with_default)

    def __bool__(self):
        return bool(self.with_default or self.without_default)


class XsdExtractor:
    def __init__(self, xml_schema: str):
        xml_obj = objectify.parse(xml_schema)
        self.root = xml_obj.getroot()
        self.file = None
        self.simple_types = []
        self.complex_types = []
        self.xml_elements = []
        self.base_types = {
            "xs:string": str,
            "xs:float": float,
            "xs:unsignedInt": int,
            "xs:anyURI": str,
            "xs:integer": int,
        }

    @property
    def xml_types(self):
        return self.simple_types + self.complex_types

    def parse_schema(self):
        with open("classes.py", "w") as file:
            self.file = file
            self.write_header()
            self.parse_simple_types(self.root)
            self.parse_complex_types(self.root)
            self.parse_root_elements()

    def write_header(self):
        self.file.write(
            '''# coding: UTF-8
""" This file contain classes corresponding to materialsdb*.xsd elements.
This file is automatically generated. Do not modify it directly.
If there is an issue or an enhancement to make then do it in dataclasses_generator.py

© All rights reserved.
ECOLE POLYTECHNIQUE FEDERALE DE LAUSANNE, Switzerland, Laboratory CNPA, 2019-2020

See the LICENSE.md file for more details.

Author : Cyril Waechter
"""
from dataclasses import dataclass
from typing import Tuple, Optional, List

'''
        )

    def parse_simple_types(self, element: objectify.ObjectifiedElement):
        simple_types = self.simple_types
        for simple_type in element.simpleType:
            pyattr = PyAttr()
            pyattr.with_default.append(f'xs_type: str = "simpleType"')
            # xs:restriction
            restrict = simple_type.restriction
            base_type = restrict.get("base")
            parent_class_name = self.get_py_type_as_string(base_type)

            name: str = simple_type.get("name")
            class_name = name if name[0].isupper() else name.title()
            simple_types.append(name)
            self.parse_enum(restrict, pyattr)
            self.parse_pattern(restrict, pyattr)
            pyattr.with_default.append(f'xml_name: str = "{name}"')
            self.file.write(
                f"""
class {class_name}({parent_class_name}):
    {str(pyattr)}

"""
            )

    def parse_enum(self, element, pyattr: PyAttr):
        """xs:enumeration"""
        values = []
        for enum in getattr(element, "enumeration", ()):
            value = enum.get("value")
            values.append(value)
        if values:
            pyattr.with_default.append(
                f"xml_enum: Tuple[str, ...] = {str(tuple(values))}"
            )

    def parse_pattern(self, element, pyattr: PyAttr):
        """xs:pattern"""
        if hasattr(element, "pattern"):
            value = element.pattern.get("value")
            pyattr.with_default.append(f'xml_pattern:str = "{value}"')

    def parse_complex_by_name(self, lookup_name, pyattr):
        for complex_type in self.root.complexType:
            name: str = complex_type.get("name")
            if name == lookup_name:
                self.parse_attrib(complex_type, pyattr)
                break
        return pyattr

    def parse_complex_types(self, element):
        complex_types = self.complex_types
        for complex_type in element.complexType:
            if complex_type in complex_types:
                continue
            pyattr = PyAttr()
            pyattr.with_default.append(f'xs_type: str = "complexType"')
            name: str = complex_type.get("name")
            class_name = self.cls_name(name)
            complex_types.append(name)
            extension = complex_type.simpleContent.extension
            base_type = extension.get("base")
            if not base_type in self.base_types.keys():
                self.parse_complex_by_name(base_type, pyattr)
            parent_class_name = self.get_py_type_as_string(base_type)
            self.parse_attrib(extension, pyattr)
            pyattr.with_default.append(
                f"xml_attributes: Tuple[str, ...] = {str(tuple(pyattr.xml_attributes))}"
            )
            pyattr.with_default.append(f'xml_name: str = "{name}"')
            self.file.write(
                f"""
class {class_name}({parent_class_name}):
    {str(pyattr)}
    def __new__(cls, object, *args, **kwargs):
        obj = {parent_class_name}.__new__(cls, object)
        for arg_name, arg_value in zip({pyattr.xml_attributes}, args):
            arg_name = arg_value
        for key, value in kwargs.items():
            if key == "object":
                continue
            setattr(obj, key, value)
        return obj

"""
            )

    def parse_attrib(self, element: objectify.ObjectifiedElement, pyattr):
        for attrib in element.iterdescendants(tag="{*}attribute"):
            name = attrib.get("name")
            pyattr.xml_attributes.append(name)
            # guess type for type hint annotation
            xml_type = attrib.get("type")
            py_type = self.get_py_type_as_string(xml_type)
            # get default value
            default_value = attrib.get("default", "")
            if default_value:
                default_value = f' = {py_type}("{default_value}")'
            # complete info with use
            use = attrib.get("use", None)
            if use == "optional":
                py_type = f"Optional[{py_type}]"
                if not default_value:
                    default_value = f" = None"

            text = f"{name}: {py_type}{default_value}"
            if default_value:
                pyattr.with_default.append(text)
            else:
                pyattr.without_default.append(text)
        return pyattr

    def get_py_type_as_string(self, xml_type: str) -> str:
        cls_name = self.cls_name(xml_type)
        if xml_type in self.xml_types:
            return cls_name
        else:
            py_type = self.base_types[xml_type]
            return py_type.__name__

    def parse_root_elements(self):
        self.elements_dict: Dict[str, objectify.ObjectifiedDataElement] = {}
        for element in getattr(self.root, "element", ()):
            self.elements_dict[element.get("ref", element.get("name"))] = element
        for element_name, element in self.elements_dict.items():
            if element_name in self.xml_elements:
                continue
            self.parse_element(element)

    def parse_element(self, element):
        pyattr = PyAttr()
        pyattr.with_default.append(f'xs_type: str = "element"')
        self.parse_attrib(element, pyattr)
        pyattr.with_default.append(
            f"xml_attributes: Tuple[str, ...] = {str(tuple(pyattr.xml_attributes))}"
        )
        for child in element.iterdescendants(tag="{*}element"):
            default_value = ""
            ref = child.get("ref", None)
            if ref:
                child_name = ref
                elem_type = self.cls_name(ref)
                if not ref in self.xml_elements:
                    self.parse_element(self.elements_dict[child_name])
                if self.is_element_list(child):
                    elem_type = f"List[{elem_type}]"
                if self.is_element_optionnal(child):
                    elem_type = f"Optional[{elem_type}]"
                    default_value = " = None"
            else:
                child_name = child.get("name")
                elem_type = self.cls_name(child.get("type"))
                if self.is_element_list(child):
                    elem_type = f"List[{elem_type}]"
            if default_value:
                pyattr.with_default.append(f"{child_name}: {elem_type}{default_value}")
            else:
                pyattr.without_default.append(f"{child_name}: {elem_type}")
            pyattr.xml_elements.append(child_name)
        name = element.get("name")
        cls_name = self.cls_name(name)
        pyattr.with_default.append(f'xml_name = "{name}"')
        pyattr.with_default.append(
            f"xml_elements: Tuple[str, ...] = {str(tuple(pyattr.xml_elements))}"
        )
        self.xml_elements.append(name)
        self.file.write(
            f"""
@dataclass
class {cls_name}:
    {str(pyattr)}

"""
        )

    def is_element_list(self, element):
        max_occurs = element.get("maxOccurs", None)
        return max_occurs == "unbounded" or int(max_occurs or 0) > 1

    def is_element_optionnal(self, element):
        min_occurs = element.get("minOccurs", None)
        return (
            min_occurs == "0"
            or element.getparent().tag == "{http://www.w3.org/2001/XMLSchema}choice"
        )

    def cls_name(self, name: str) -> str:
        return name if name[0].isupper() else name.title()


def main():
    xml_schema = str(Path("../src/materialsdb/schema/materialsdb103.xsd"))
    xsd_extractor = XsdExtractor(xml_schema)
    xsd_extractor.parse_schema()


if __name__ == "__main__":
    main()