"""Map resolved payloads onto an ifcopenshell file via ifcopenshell.api.

Pure ifcopenshell (no bpy/bonsai imports) so CI can test it directly.
api handles GlobalId/OwnerHistory and keeps entities schema-valid; kwargs
target the 0.9 api (add_layer takes no thickness — edit_layer sets it;
root.create_entity takes ifc_class=)."""

import ifcopenshell.api


def _materials_of(pset):
    materials = pset.Material
    if not isinstance(materials, (list, tuple)):
        materials = [materials]
    return materials


def _pset_props(pset):
    return {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}


def existing_materials_by_id(file):
    """material_id -> first IfcMaterial carrying a matching materialsdb pset."""
    found = {}
    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != "materialsdb":
            continue
        material_id = _pset_props(pset).get("material_id")
        for material in _materials_of(pset):
            found.setdefault(material_id, material)
    return found


def _existing_keys(file):
    """(material_id, layer_id | None) pairs already present, from the
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


def _add_identity_pset(file, material, identity):
    pset = ifcopenshell.api.run("pset.add_pset", file, product=material, name="materialsdb")
    ifcopenshell.api.run(
        "pset.edit_pset",
        file,
        pset=pset,
        properties={
            "material_id": identity["material_id"],
            "company_id": identity.get("company_id") or "",
            "company": identity.get("company") or "",
        },
    )


def _add_property_psets(file, material, psets):
    for pset_name, props in (psets or {}).items():
        pset = ifcopenshell.api.run("pset.add_pset", file, product=material, name=str(pset_name))
        ifcopenshell.api.run("pset.edit_pset", file, pset=pset, properties=props)


def _apply_style(file, material_entry, material):
    """Best-effort schema-valid material styling; needs a representation
    context (bonsai files have one, scratch CI files do not)."""
    color = material_entry.get("color")
    if not color:
        return
    try:
        contexts = file.by_type("IfcRepresentationContext")
        if not contexts:
            return
        color = int(color)
        name = f"color {color}"
        styles = {style.Name: style for style in file.by_type("IfcSurfaceStyle")}
        style = styles.get(name)
        if style is None:
            style = ifcopenshell.api.run("style.add_style", file, name=name)
            ifcopenshell.api.run(
                "style.add_surface_style",
                file,
                style=style,
                ifc_class="IfcSurfaceStyleShading",
                attributes={
                    "SurfaceColour": file.create_entity(
                        "IfcColourRgb",
                        Name=None,
                        Red=(color >> 16) / 255,
                        Green=((color >> 8) & 255) / 255,
                        Blue=(color & 255) / 255,
                    )
                },
            )
        ifcopenshell.api.run("style.assign_material_style", file, material=material, style=style, context=contexts[0])
    except Exception:  # noqa: BLE001, S110 - cosmetic; never fail the insert
        pass


def apply_add_materials(file, payload) -> int:
    """Create IfcMaterial (+ identity/property psets, layer + set, style) per
    payload entry. Entries whose (material_id, layer_id) key already exists
    are skipped. Returns the number created."""
    existing = _existing_keys(file)
    created = 0
    for entry in payload.get("materials") or []:
        identity = entry["identity"]
        layer = entry.get("layer") or {}
        org_layer_id = (entry.get("psets") or {}).get("materialsdb.org_layer", {}).get("layer_id")
        key = (identity["material_id"], layer.get("layer_id") or org_layer_id)
        if key in existing:
            continue
        material = ifcopenshell.api.run(
            "material.add_material",
            file,
            name=str(entry["name"]),
            category=str(entry.get("category") or ""),
            description=str(entry.get("description") or ""),
        )
        _add_identity_pset(file, material, identity)
        _add_property_psets(file, material, entry.get("psets"))
        if layer:
            thickness = float(layer["thick_m"])
            label = f"{entry['name']} | {round(thickness * 1000)}mm"
            layer_set = ifcopenshell.api.run(
                "material.add_material_set", file, name=label, set_type="IfcMaterialLayerSet"
            )
            ifc_layer = ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=material)
            ifcopenshell.api.run(
                "material.edit_layer",
                file,
                layer=ifc_layer,
                attributes={"LayerThickness": thickness, "Name": label, "Description": str(layer["layer_id"])},
            )
        _apply_style(file, entry, material)
        existing.add(key)
        created += 1
    return created


def apply_add_construction(file, payload) -> dict:
    """Upsert a construction as typed element(s) + IfcMaterialLayerSet.

    Types are created if missing and kept if present; a re-send rebuilds the
    existing set IN PLACE (same entity, old layers removed) and re-assigns
    all targets onto it. Generic usage shares ONE set across all types via a
    single IfcRelAssociatesMaterial. Materials are found by their materialsdb
    identity pset or created minimally (identity + style, no per-layer
    psets)."""
    construction = payload["construction"]
    summary = {"types_created": 0, "sets_updated": 0, "materials_created": 0}

    known = existing_materials_by_id(file)
    layers = []
    for layer in construction["layers"]:
        material = known.get(layer["material_id"])
        if material is None:
            entry = layer["material"]
            material = ifcopenshell.api.run(
                "material.add_material",
                file,
                name=str(entry["name"]),
                category=str(entry.get("category") or ""),
                description=str(entry.get("description") or ""),
            )
            _add_identity_pset(file, material, entry["identity"])
            _apply_style(file, entry, material)
            known[layer["material_id"]] = material
            summary["materials_created"] += 1
        layers.append((layer, material))

    name = str(construction["name"])
    targets = []
    for cls in construction["types"]:
        match = [t for t in file.by_type(cls) if t.Name == name]
        if match:
            targets.append(match[0])
        else:
            targets.append(ifcopenshell.api.run("root.create_entity", file, ifc_class=cls, name=name))
            summary["types_created"] += 1

    the_set = None
    for target in targets:
        for association in target.HasAssociations or ():
            relating = getattr(association, "RelatingMaterial", None)
            if relating is not None and relating.is_a("IfcMaterialLayerSet"):
                the_set = relating
                break
        if the_set is not None:
            break

    if the_set is None:
        the_set = ifcopenshell.api.run("material.add_material_set", file, name=name, set_type="IfcMaterialLayerSet")
    else:
        summary["sets_updated"] += 1
        for stale in list(the_set.MaterialLayers or ()):
            ifcopenshell.api.run("material.remove_layer", file, layer=stale)

    for layer, material in layers:
        ifc_layer = ifcopenshell.api.run("material.add_layer", file, layer_set=the_set, material=material)
        mm = round(float(layer["thickness_m"]) * 1000)
        ifcopenshell.api.run(
            "material.edit_layer",
            file,
            layer=ifc_layer,
            attributes={
                "LayerThickness": float(layer["thickness_m"]),
                "Name": f"{layer['material']['name']} | {mm}mm",
                "Description": str(layer["material_id"]),
            },
        )

    for target in targets:
        for association in list(target.HasAssociations or ()):
            file.remove(association)
    ifcopenshell.api.run(
        "material.assign_material",
        file,
        products=[t for t in targets if t is not None],
        type="IfcMaterialLayerSet",
        material=the_set,
    )
    return summary
