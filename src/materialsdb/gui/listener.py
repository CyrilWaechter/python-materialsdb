"""Resolved push payloads for the listener channel (slice 1: add_materials).

The server resolves picker selections into exactly the entity list
MaterialBuilder would create (one entry per IfcMaterial); the Bonsai add-on
maps the payload onto its open file without importing this library."""

from materialsdb import config, utils
from materialsdb.ifc.material_builder import PSETS, get_value


def _resolved_psets(layer, country) -> dict:
    resolved = {}
    for pset_name, props in PSETS.items():
        values = {}
        for prop_name, definition in props.items():
            if not definition["primary_measure_type"]:
                continue
            value = get_value(layer, definition, country)
            if value:
                values[prop_name] = value * (definition.get("unit_factor") or 1)
        if values:
            resolved[pset_name] = values
    return resolved


def build_add_materials_payload(store_, items, country=None, lang=None):
    """Resolve picker items [{id, layer_ids?}] into
    ({"action": "add_materials", "materials": [...]}, missing_ids).

    One materials[] entry per IfcMaterial, mirroring MaterialBuilder.build():
    a 2-layer material yields 2 same-named entries, each carrying its own
    layer's resolved psets. Ids that fail to resolve (unknown, or no
    resolvable layers) land in missing_ids."""
    country = country or config.get_country()
    lang = lang or config.get_lang()
    materials = []
    missing = []
    for item in items:
        if not isinstance(item, dict):
            missing.append(str(item))
            continue
        material_id = str(item.get("id") or "")
        summary = store_.get_summary(material_id)
        material = store_.get(material_id)
        if summary is None or material is None:
            missing.append(material_id)
            continue
        wanted = {str(g) for g in item.get("layer_ids") or []} or None
        name = str(utils.get_material_name(material, lang))
        description = str(utils.get_material_description(material, lang))
        category = str(getattr(material.information, "group", "") or "")
        color = getattr(material.information, "color", None)
        entries = []
        for layer in utils.get_material_layers(material):
            if wanted is not None and str(layer.id) not in wanted:
                continue
            geometry = utils.get_by_country(layer.geometry or (), country)
            thick = getattr(geometry, "thick", None)
            entries.append(
                {
                    "source_id": material_id,
                    "layer_id": str(layer.id),
                    "name": name,
                    "description": description,
                    "category": category,
                    "color": int(color) if color else None,
                    "identity": {
                        "material_id": material_id,
                        "company_id": str(summary.company_id or ""),
                        "company": str(summary.company or ""),
                    },
                    "psets": _resolved_psets(layer, country),
                    "layer": {"layer_id": str(layer.id), "thick_m": thick / 1000} if thick else None,
                }
            )
        if not entries:
            missing.append(material_id)
            continue
        materials.extend(entries)
    return {"action": "add_materials", "materials": materials}, missing


_DESIGN_USAGE_TO_TYPES = {
    "consDesignForWall": ["IfcWallType"],
    "consDesignForFloor": ["IfcSlabType"],
    "consDesignForRoof": ["IfcRoofType"],
}
GENERIC_TYPES = ["IfcWallType", "IfcSlabType", "IfcRoofType"]


def build_add_construction_payload(store_, body, country=None, lang=None):
    """Resolve a construction body into a self-sufficient add_construction
    payload (or (None, problems) when validation fails). Materials travel
    minimal — identity/name/category/color, to_ifc_layer_set-style — and
    find-or-create on the add-on side keys on the identity pset."""
    from materialsdb import construction as cm

    body = dict(body or {})
    if body.get("design_usage") not in (None, *_DESIGN_USAGE_TO_TYPES):
        body["design_usage"] = None  # unknown usage -> generic types (validate_construction would reject it)
    construction, problems = cm.validate_construction(body, store_)
    if problems:
        return None, problems
    country = country or config.get_country()
    lang = lang or config.get_lang()
    layers = []
    for layer in construction.layers:
        if layer.placeholder is not None:
            layers.append(
                {
                    "material_id": None,
                    "thickness_m": layer.thickness_m,
                    "placeholder": dict(layer.placeholder),
                }
            )
            continue
        summary = store_.get_summary(layer.material_id)
        material = store_.get(layer.material_id)
        if summary is None or material is None:
            problems.append(f"unknown material id: {layer.material_id}")
            continue
        color = getattr(material.information, "color", None)
        layers.append(
            {
                "material_id": layer.material_id,
                "thickness_m": layer.thickness_m,
                "material": {
                    "source_id": layer.material_id,
                    "name": str(utils.get_material_name(material, lang)),
                    "description": str(utils.get_material_description(material, lang)),
                    "category": str(getattr(material.information, "group", "") or ""),
                    "color": int(color) if color else None,
                    "identity": {
                        "material_id": layer.material_id,
                        "company_id": str(summary.company_id or ""),
                        "company": str(summary.company or ""),
                    },
                },
            }
        )
    if problems:
        return None, problems
    return {
        "action": "add_construction",
        "construction": {
            "name": construction.name,
            "design_usage": construction.design_usage,
            "types": _DESIGN_USAGE_TO_TYPES.get(construction.design_usage or "", GENERIC_TYPES),
            "layers": layers,
        },
    }, []
