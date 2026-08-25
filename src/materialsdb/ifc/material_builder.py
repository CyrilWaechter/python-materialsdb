"""Shared per-material IFC entity creation for batch and single export."""

import json
from pathlib import Path

from materialsdb import config, utils

MATERIALSDB_PSET = "materialsdb"

CATEGORIES = {
    "Others": {"hatch": "", "color": (255, 255, 255)},
    "Water_Proof": {"hatch": "", "color": (255, 255, 255)},
    "Vapour_Proof": {"hatch": "", "color": (0, 0, 0)},
    "Concrete": {"hatch": "", "color": (0, 255, 0)},
    "Wood_Timberproducts": {"hatch": "", "color": (91, 60, 17)},
    "Insulation": {"hatch": "", "color": (253, 108, 158)},
    "Masonry": {"hatch": "", "color": (253, 70, 38)},
    "Metal": {"hatch": "", "color": (119, 181, 254)},
    "Mortar": {"hatch": "", "color": (102, 0, 153)},
    "Plastics": {"hatch": "", "color": (96, 96, 96)},
    "Stone": {"hatch": "", "color": (0, 0, 255)},
    "Composite": {"hatch": "", "color": (112, 141, 35)},
    "Films": {"hatch": "", "color": (0, 0, 0)},
    "Render": {"hatch": "", "color": (0, 0, 0)},
    "Covering": {"hatch": "", "color": (0, 0, 0)},
    "Glas": {"hatch": "", "color": (27, 79, 8)},
    "Soil": {"hatch": "", "color": (142, 84, 52)},
}


def clean_psets(psets):
    new_dict = {}
    for pset_name, props in psets.items():
        pset_dict = {}
        for prop_name, definition in props.items():
            if definition["path"]:
                pset_dict[prop_name] = definition
        if pset_dict:
            new_dict[pset_name] = pset_dict
    return new_dict


PSETS = json.loads(Path(__file__).with_name("material_psets.json").read_text("utf-8"))
PSETS = clean_psets(PSETS)


def get_value(layer, definition, country=None):
    value = layer
    for attrib in definition["path"]:
        value = getattr(value, attrib)
        if isinstance(value, list):
            value = utils.get_by_country(value, country)
            if not value:
                return None
    return value


def _ifc_text(file, value):
    return file.create_entity("IfcText", str(value))


class MaterialBuilder:
    def __init__(self, file, country=None, lang=None):
        self.file = file
        self.country = country or config.get_country()
        self.lang = lang or config.get_lang()
        self._context = None
        self._styles = {}

    def build(self, material, company_id="", company="", verxml=None, layer_ids=None):
        name = utils.get_material_name(material, self.lang)
        description = utils.get_material_description(material, self.lang)
        category = str(material.information.group or "")
        color = material.information.color
        surface_style = self.get_surface_style(color, category)
        styled_item = self.file.createIfcStyledItem(Styles=[surface_style])
        self.file.createIfcStyledRepresentation(
            ContextOfItems=self._get_context(),
            RepresentationIdentifier="Body",
            Items=[styled_item],
        )
        created = []
        wanted = {str(g) for g in layer_ids} if layer_ids is not None else None
        for layer in utils.get_material_layers(material):
            if wanted is not None and str(layer.id) not in wanted:
                continue
            ifc_material = self.file.createIfcMaterial(str(name), str(description), category)
            created.append(ifc_material)
            self._create_identity_pset(ifc_material, material, company_id, company, verxml)
            self._create_property_psets(ifc_material, layer)
            geometry = utils.get_by_country(layer.geometry or (), self.country)
            thick = getattr(geometry, "thick", None)
            if thick:
                element_name = f"{name} | {thick}mm"
                ifc_layer = self.file.create_entity(
                    "IfcMaterialLayer",
                    Material=ifc_material,
                    LayerThickness=thick / 1000,
                    Name=str(element_name),
                    Description=str(layer.id),
                )
                self.file.create_entity(
                    "IfcMaterialLayerSet",
                    MaterialLayers=[ifc_layer],
                    LayerSetName=str(element_name),
                )
        return created

    def find_existing(self, material_id):
        for pset in self.file.by_type("IfcMaterialProperties"):
            if pset.Name != MATERIALSDB_PSET:
                continue
            if self._pset_id(pset) == str(material_id):
                materials = pset.Material
                if not isinstance(materials, (list, tuple)):
                    materials = [materials]
                return materials[0]
        return None

    @staticmethod
    def _pset_id(pset):
        for prop in pset.Properties:
            if prop.Name == "material_id":
                return prop.NominalValue.wrappedValue
        return None

    def _get_context(self):
        if self._context is None:
            self._context = self.file.createIfcRepresentationContext()
        return self._context

    def _create_identity_pset(self, ifc_material, material, company_id, company, verxml):
        properties = [
            self.file.create_entity(
                "IfcPropertySingleValue", Name="material_id", NominalValue=_ifc_text(self.file, material.id)
            ),
            self.file.create_entity(
                "IfcPropertySingleValue", Name="company_id", NominalValue=_ifc_text(self.file, company_id)
            ),
            self.file.create_entity(
                "IfcPropertySingleValue", Name="company", NominalValue=_ifc_text(self.file, company)
            ),
        ]
        if verxml is not None:
            properties.append(
                self.file.create_entity(
                    "IfcPropertySingleValue",
                    Name="verxml",
                    NominalValue=self.file.create_entity("IfcInteger", int(verxml)),
                )
            )
        self.file.create_entity(
            "IfcMaterialProperties",
            Name=MATERIALSDB_PSET,
            Properties=properties,
            Material=ifc_material,
        )

    def _create_property_psets(self, ifc_material, layer):
        for pset_name, props in PSETS.items():
            properties = []
            for prop_name, definition in props.items():
                primary_measure_type = definition["primary_measure_type"]
                if not primary_measure_type:
                    continue
                value = get_value(layer, definition, self.country)
                if value:
                    unit_factor = definition.get("unit_factor", None) or 1
                    properties.append(
                        self.file.create_entity(
                            "IfcPropertySingleValue",
                            Name=prop_name,
                            NominalValue=self.file.create_entity(primary_measure_type, value * unit_factor),
                        )
                    )
            if not properties:
                continue
            self.file.create_entity(
                "IfcMaterialProperties",
                Name=pset_name,
                Properties=properties,
                Material=ifc_material,
            )

    def get_surface_style(self, color, category):
        if color:
            name = f"color {color}"
        else:
            if not category or category not in CATEGORIES:
                category = "Others"
            name = f"category {category}"
        if name in self._styles and self._styles[name] in self.file:
            return self._styles[name]
        if color:
            style = self.file.createIfcSurfaceStyleShading(SurfaceColour=self.color_xml_to_ifc(color))
        else:
            style = self.file.createIfcSurfaceStyleShading(
                SurfaceColour=self.file.createIfcColourRgb(None, *CATEGORIES[category]["color"]),
            )
        surface_style = self.file.createIfcSurfaceStyle(Name=name, Side="BOTH", Styles=[style])
        self._styles[name] = surface_style
        return surface_style

    def color_xml_to_ifc(self, color: int):
        """Color definition in xml is obscur. We assume that it is a decimal color.
        See: https://stackoverflow.com/a/2262152/4098083"""
        return self.file.createIfcColourRgb(Blue=color & 255, Green=(color >> 8) & 255, Red=(color >> 16) & 255)


def _materials_of(pset):
    materials = pset.Material
    if not isinstance(materials, (list, tuple)):
        materials = [materials]
    return materials


def purge_material(file, material) -> None:
    """Remove one IfcMaterial and everything the builder created for it.

    `material` is the guid string of an IfcMaterial currently present in the
    file (passing a stale/removed wrapper would segfault ifcopenshell).
    Shared surface styles are intentionally kept."""
    target = {m.id() for m in file.by_type("IfcMaterial") if m.id() == material}
    for pset in list(file.by_type("IfcMaterialProperties")):
        if {m.id() for m in _materials_of(pset)} & target:
            # file.remove() does not cascade to pset.Properties on ifcopenshell
            # 0.8: delete each property explicitly or they leak as orphans
            for prop in list(pset.Properties):
                file.remove(prop)
            file.remove(pset)
    for layer in list(file.by_type("IfcMaterialLayer")):
        if layer.Material is not None and layer.Material.id() in target:
            layer_sets = getattr(layer, "ToMaterialLayerSet", None) or ()
            file.remove(layer)
            for parent in layer_sets:
                if not parent.MaterialLayers:
                    file.remove(parent)
    for material in [m for m in file.by_type("IfcMaterial") if m.id() in target]:
        file.remove(material)


def add_material(file, material, company_id="", company="", verxml=None, replace=False, layer_ids=None):
    builder = MaterialBuilder(file)
    existing = builder.find_existing(str(material.id))
    if existing and not replace:
        return existing
    if replace:
        # one source material can yield several IfcMaterial entities (one per
        # layer): purge them all before rebuilding
        while existing is not None:
            purge_material(file, existing.id())
            existing = builder.find_existing(str(material.id))
    created = builder.build(material, company_id=company_id, company=company, verxml=verxml, layer_ids=layer_ids)
    return created[0] if created else None


def create_material_file(material_id, schema="IFC4", store_=None):
    from materialsdb import query

    store_ = store_ or query.get_store()
    summary = store_.get_summary(material_id)
    if summary is None:
        raise KeyError(f"Unknown materialsdb material id: {material_id}")
    material = store_.get(material_id)

    from materialsdb.ifc.project_library import ProjectLibrary

    library = ProjectLibrary(schema=schema)
    library.create_project_library(
        company=summary.company,
        companyid=summary.company_id or "",
        ver=1,  # single-material files carry no source revision
        crd=utils.new_tdatetime(),
    )
    add_material(library.file, material, company_id=summary.company_id or "", company=summary.company)
    return library.file
