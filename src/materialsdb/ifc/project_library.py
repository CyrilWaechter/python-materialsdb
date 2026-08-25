import uuid

import ifcopenshell
import ifcopenshell.api

from materialsdb import config, utils
from materialsdb.classes import (
    Materials,
)
from materialsdb.ifc.material_builder import (
    CATEGORIES,
    MATERIALSDB_PSET,
    PSETS,
    MaterialBuilder,
    clean_psets,
    get_value,
)
from materialsdb.serialiser import XmlDeserialiser

# Re-exports: external code may import these from project_library (historical API).
__all__ = [
    "CATEGORIES",
    "MATERIALSDB_PSET",
    "PSETS",
    "MaterialBuilder",
    "ProjectLibrary",
    "clean_psets",
    "create_project_library_from_xml",
    "get_value",
]


class ProjectLibrary:
    def __init__(self, schema: str = "IFC4"):
        self.file = ifcopenshell.file(schema=schema)
        self.application = self.create_application()
        self.project_library = None
        self.lang = config.get_lang()
        self.country = config.get_country()
        self.owner_history = None
        self.builder = MaterialBuilder(self.file, country=self.country, lang=self.lang)

    def create_application(self):
        file = self.file

        # https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/link/ifcaddress.htm
        address = file.createIfcAddress(
            Purpose="OFFICE",
            Description="Anton Philipslaan 199\n5616TW Eindhoven\nThe Netherlands",
        )

        # https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/link/ifcorganization.htm
        organisation = file.createIfcOrganization(
            Name="AECGeeks",
            Description="""Software development and consultancy for the
            Architecture Engineering and Construction industry""",
            Addresses=[address],
        )

        # https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/link/ifcapplication.htm
        return file.createIfcApplication(
            ApplicationDeveloper=organisation,
            Version=ifcopenshell.version,
            ApplicationFullName="IfcOpenShell",
            ApplicationIdentifier="ifcopenshell",
        )

    def create_project_library(self, source: Materials, role: str = "MANUFACTURER"):
        file = self.file

        # https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/link/ifcroleenum.htm
        role = file.createIfcActorRole(role)

        # https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/link/ifcperson.htm
        person = file.createIfcPerson(
            Identification=str(uuid.uuid4()),
            FamilyName="Unknown",
            GivenName="Unknown",
            Roles=[role],
        )

        # https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/link/ifcorganization.htm
        organisation = file.createIfcOrganization(Identification=source.companyid, Name=source.company, Roles=[role])

        # https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/link/ifcpersonandorganization.htm
        person_and_organisation = file.createIfcPersonAndOrganization(person, organisation, Roles=[role])

        # https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/link/ifcownerhistory.htm
        owner_history = file.createIfcOwnerHistory(
            OwningUser=person_and_organisation,
            OwningApplication=self.application,
            CreationDate=max(int(utils.date_from_xml(source.crd).timestamp()), -2147483648),
        )
        self.owner_history = owner_history

        # https://standards.buildingsmart.org/IFC/RELEASE/IFC4/ADD2_TC1/HTML/link/ifcprojectlibrary.htm
        file.createIfcProjectLibrary(
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=owner_history,
            Name=source.company,
            Description=f"Material library converted from materialsdb xml for company {source.company}",
            ObjectType="MaterialLibrary",
            LongName=f"{source.company} version {source.ver}",
        )

    def create_materials(self, source: Materials):
        for material in utils.get_materials(source, self.country):
            created = self.builder.build(material)
            if not created:
                continue
            name = utils.get_material_name(material, self.lang)
            for layer, ifc_material in zip(utils.get_material_layers(material), created):
                geometry = utils.get_by_country(layer.geometry or (), self.country)
                thick = getattr(geometry, "thick", None)
                element_name = f"{name} | {thick}mm" if thick else name
                assigned_material = self._assigned_material(ifc_material, thick, element_name, name)
                self._create_usage_types(material, assigned_material, element_name)

    def _assigned_material(self, ifc_material, thick, element_name, name):
        if not thick:
            return ifc_material
        # MaterialBuilder.build already created the layer + layer set for this
        # layer; reuse them so batch export does not duplicate entities.
        for rel in self.file.get_inverse(ifc_material):
            if rel.is_a("IfcMaterialLayer"):
                for layer_set in rel.ToMaterialLayerSet or ():
                    return layer_set
        ifc_layer = self.file.create_entity(
            "IfcMaterialLayer",
            Material=ifc_material,
            LayerThickness=thick / 1000,
            Name=str(element_name),
        )
        return self.file.create_entity(
            "IfcMaterialLayerSet",
            MaterialLayers=[ifc_layer],
            LayerSetName=str(element_name),
        )

    def _create_usage_types(self, material, assigned_material, element_name):
        file = self.file
        kind_map = (("wall", "IfcWallType"), ("roof", "IfcRoofType"), ("floor", "IfcRoofType"), ("door", "IfcDoorType"))
        for flag, entity_type in kind_map:
            if getattr(material.information, flag, None):
                product = file.create_entity(entity_type, GlobalId=ifcopenshell.guid.new(), Name=str(element_name))
                ifcopenshell.api.run(
                    "material.assign_material",
                    file,
                    products=[product],
                    material=assigned_material,
                )

    def get_surface_style(self, color, category):
        return self.builder.get_surface_style(color, category)

    def color_xml_to_ifc(self, color: int):
        return self.builder.color_xml_to_ifc(color)


def create_project_library_from_xml(xml_path):
    library = ProjectLibrary()
    deserialiser = XmlDeserialiser()
    source = deserialiser.from_xml(str(xml_path))
    library.create_project_library(source)
    library.create_materials(source)
    return library.file


def main():
    file = create_project_library_from_xml("example_v103.xml")
    file.write("example_v103.xml")


if __name__ == "__main__":
    main()
