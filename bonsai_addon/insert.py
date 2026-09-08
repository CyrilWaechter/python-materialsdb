"""Map resolved add_materials payloads onto an ifcopenshell file.

Pure ifcopenshell (no bpy/bonsai imports) so CI can test it directly.
Creation mirrors materialsdb.ifc.material_builder.MaterialBuilder so
listener-inserted materials are indistinguishable from exported ones."""

import ifcopenshell  # noqa: F401 - documents the runtime requirement


def _materials_of(pset):
    materials = pset.Material
    if not isinstance(materials, (list, tuple)):
        materials = [materials]
    return materials


def _pset_props(pset):
    return {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}


def _existing_keys(file):
    """(material_id, layer_id | None) pairs already present, read from the
    materialsdb identity + org_layer psets."""
    layer_of = {}
    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != "materialsdb.org_layer":
            continue
        layer_id = _pset_props(pset).get("layer_id")
        for material in _materials_of(pset):
            layer_of[material.id()] = layer_id
    keys = set()
    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != "materialsdb":
            continue
        material_id = _pset_props(pset).get("material_id")
        for material in _materials_of(pset):
            keys.add((material_id, layer_of.get(material.id())))
    return keys


def _ifc_text(file, value):
    return file.create_entity("IfcText", str(value))


def _nominal(file, value):
    if isinstance(value, bool):
        return file.create_entity("IfcBoolean", value)
    if isinstance(value, str):
        return _ifc_text(file, value)
    return file.create_entity("IfcReal", float(value))


def apply_add_materials(file, payload) -> int:
    """Create IfcMaterial (+ identity/property psets, layer + set, surface
    style) for each payload entry. Entries whose (material_id, layer_id) key
    is already present are skipped. Returns the number created."""
    existing = _existing_keys(file)
    created = 0
    for entry in payload.get("materials") or []:
        identity = entry["identity"]
        layer = entry.get("layer") or {}
        key = (identity["material_id"], layer.get("layer_id"))
        if key in existing:
            continue
        _apply_entry(file, entry)
        existing.add(key)
        created += 1
    return created


def _apply_entry(file, entry):
    material = file.createIfcMaterial(
        str(entry["name"]), str(entry.get("description") or ""), str(entry.get("category") or "")
    )
    identity = entry["identity"]
    file.create_entity(
        "IfcMaterialProperties",
        Name="materialsdb",
        Properties=[
            file.create_entity(
                "IfcPropertySingleValue", Name="material_id", NominalValue=_ifc_text(file, identity["material_id"])
            ),
            file.create_entity(
                "IfcPropertySingleValue",
                Name="company_id",
                NominalValue=_ifc_text(file, identity.get("company_id") or ""),
            ),
            file.create_entity(
                "IfcPropertySingleValue", Name="company", NominalValue=_ifc_text(file, identity.get("company") or "")
            ),
        ],
        Material=material,
    )
    for pset_name, props in (entry.get("psets") or {}).items():
        properties = [
            file.create_entity("IfcPropertySingleValue", Name=str(name), NominalValue=_nominal(file, value))
            for name, value in props.items()
        ]
        file.create_entity("IfcMaterialProperties", Name=str(pset_name), Properties=properties, Material=material)
    layer = entry.get("layer")
    if layer:
        thickness = float(layer["thick_m"])
        label = f"{entry['name']} | {round(thickness * 1000)}mm"
        ifc_layer = file.create_entity(
            "IfcMaterialLayer",
            Material=material,
            LayerThickness=thickness,
            Name=label,
            Description=str(layer["layer_id"]),
        )
        file.create_entity("IfcMaterialLayerSet", MaterialLayers=[ifc_layer], LayerSetName=label)
    _apply_style(file, entry)


def _apply_style(file, entry):
    """Best-effort surface style from the raw XML color decimal; identical
    floating-chain shape to the export path (styled item without Item)."""
    color = entry.get("color")
    if not color:
        return
    try:
        color = int(color)
        shading = file.createIfcSurfaceStyleShading(
            SurfaceColour=file.createIfcColourRgb(Blue=color & 255, Green=(color >> 8) & 255, Red=(color >> 16) & 255)
        )
        style = file.createIfcSurfaceStyle(Name=f"color {color}", Side="BOTH", Styles=[shading])
        file.createIfcStyledItem(Styles=[style])
    except Exception:  # noqa: BLE001, S110 - cosmetic; never fail the insert
        pass
