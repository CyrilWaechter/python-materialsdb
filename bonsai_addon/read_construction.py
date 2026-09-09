"""Read a construction (type + IfcMaterialLayerSet) from a Bonsai model.

Pure ifcopenshell + utils (no bpy/bonsai imports) so CI can test it."""

import ifcopenshell.util.element


class ReadError(ValueError):
    """The selected element cannot yield a layer-set construction."""


_DESIGN_USAGE_BY_CLASS = {
    "IfcWallType": "consDesignForWall",
    "IfcSlabType": "consDesignForFloor",
    "IfcRoofType": "consDesignForRoof",
    "IfcWall": "consDesignForWall",
    "IfcSlab": "consDesignForFloor",
    "IfcRoof": "consDesignForRoof",
}


def read_construction_from_element(element) -> dict:
    if element.is_a("IfcTypeProduct"):
        type_element = element
    else:
        type_element = ifcopenshell.util.element.get_type(element)
        if type_element is None:
            raise ReadError("selected element has no type")
    material = ifcopenshell.util.element.get_material(type_element)
    if material is None:
        raise ReadError("type has no material")
    if not material.is_a("IfcMaterialLayerSet"):
        raise ReadError(f"material is not an IfcMaterialLayerSet: {material.is_a()}")
    layers = []
    for layer in material.MaterialLayers or ():
        thickness = layer.LayerThickness
        entry = {
            "material_id": None,
            "thickness_m": float(thickness) if thickness else 0.0,
            "placeholder": None,
        }
        if layer.Material is not None:
            psets = ifcopenshell.util.element.get_psets(layer.Material)
            material_id = psets.get("materialsdb", {}).get("material_id")
            if material_id:
                entry["material_id"] = str(material_id)
            else:
                entry["placeholder"] = {
                    "name": str(layer.Material.Name or ""),
                    "lambda_value": psets.get("Pset_MaterialThermal", {}).get("ThermalConductivity"),
                }
        else:
            entry["placeholder"] = {"name": "", "lambda_value": None}
        layers.append(entry)
    return {
        "name": str(type_element.Name or ""),
        "design_usage": _DESIGN_USAGE_BY_CLASS.get(type_element.is_a()),
        "layers": layers,
    }
