"""Map resolved payloads onto an ifcopenshell file via ifcopenshell.api.

Pure ifcopenshell (no bpy/bonsai imports) so CI can test it directly.
api handles GlobalId/OwnerHistory and keeps entities schema-valid; kwargs
target the 0.9 api (add_layer takes no thickness — edit_layer sets it;
root.create_entity takes ifc_class=)."""

import math

import ifcopenshell.api
import ifcopenshell.util.element

_THERMAL_TRANSMITTANCE_PSET = {
    "IfcWallType": "Pset_WallCommon",
    "IfcSlabType": "Pset_SlabCommon",
    "IfcRoofType": "Pset_RoofCommon",
}


def _write_thermal_transmittance(file, target, u_value: float) -> bool:
    """Find-or-create the type's Common pset and set ThermalTransmittance."""
    pset_name = _THERMAL_TRANSMITTANCE_PSET.get(target.is_a())
    if pset_name is None:
        return False
    found = ifcopenshell.util.element.get_pset(target, name=pset_name)
    if found is None:
        pset = ifcopenshell.api.run("pset.add_pset", file, product=target, name=pset_name)
    else:
        pset = file.by_id(found["id"])
    ifcopenshell.api.run("pset.edit_pset", file, pset=pset, properties={"ThermalTransmittance": u_value})
    return True


def _material_id_of(material) -> str | None:
    return ifcopenshell.util.element.get_psets(material).get("materialsdb", {}).get("material_id")


def _org_layer_id_of(material) -> str | None:
    return ifcopenshell.util.element.get_psets(material).get("materialsdb.org_layer", {}).get("layer_id")


def existing_materials_by_id(file):
    """material_id -> first IfcMaterial carrying a matching materialsdb pset."""
    found = {}
    for material in file.by_type("IfcMaterial"):
        material_id = _material_id_of(material)
        if material_id is not None and material_id not in found:
            found[material_id] = material
    return found


def _existing_keys(file):
    """(material_id, layer_id | None) pairs already present, from the
    materialsdb identity + org_layer psets."""
    keys = set()
    for material in file.by_type("IfcMaterial"):
        material_id = _material_id_of(material)
        if material_id is not None:
            keys.add((material_id, _org_layer_id_of(material)))
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


def _find_material_by_name(file, name):
    if not name:
        return None
    return next((m for m in file.by_type("IfcMaterial") if m.Name == name), None)


def _create_placeholder_material(file, placeholder):
    material = ifcopenshell.api.run("material.add_material", file, name=str(placeholder.get("name") or "(unnamed)"))
    lambda_value = placeholder.get("lambda_value")
    if lambda_value:
        thermal = ifcopenshell.api.run("pset.add_pset", file, product=material, name="Pset_MaterialThermal")
        ifcopenshell.api.run(
            "pset.edit_pset", file, pset=thermal, properties={"ThermalConductivity": float(lambda_value)}
        )
    return material


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
    psets). Placeholder layers (material_id None) re-attach a model material
    by name or create one, carrying a Pset_MaterialThermal when a λ is
    given. A `u_values` map (per type class) writes `ThermalTransmittance`
    into the matching `Pset_<Type>Common` pset, created or edited in place."""
    construction = payload["construction"]
    summary = {"types_created": 0, "sets_updated": 0, "materials_created": 0, "placeholders_matched": 0}

    known = existing_materials_by_id(file)
    layers = []
    for layer in construction["layers"]:
        if layer.get("placeholder") is not None:
            name = str(layer["placeholder"].get("name") or "")
            material = _find_material_by_name(file, name)
            if material is None:
                material = _create_placeholder_material(file, layer["placeholder"])
                summary["materials_created"] += 1
            else:
                summary["placeholders_matched"] += 1
            layers.append((layer, material))
            continue
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
        entry = layer.get("material")
        if entry is not None:
            label = f"{entry['name']} | {mm}mm"
            description = str(layer["material_id"])
        else:
            label = f"{(layer.get('placeholder') or {}).get('name') or '(unnamed)'} | {mm}mm"
            description = ""
        ifcopenshell.api.run(
            "material.edit_layer",
            file,
            layer=ifc_layer,
            attributes={
                "LayerThickness": float(layer["thickness_m"]),
                "Name": label,
                "Description": description,
            },
        )

    for target in targets:
        for association in list(target.HasAssociations or ()):
            if association.is_a("IfcRelAssociatesMaterial"):
                file.remove(association)
    ifcopenshell.api.run(
        "material.assign_material",
        file,
        products=[t for t in targets if t is not None],
        type="IfcMaterialLayerSet",
        material=the_set,
    )
    raw_u_values = construction.get("u_values")
    u_values = raw_u_values if isinstance(raw_u_values, dict) else {}
    written = 0
    for target in targets:
        u_value = u_values.get(target.is_a())
        if (
            isinstance(u_value, (int, float))
            and not isinstance(u_value, bool)
            and math.isfinite(float(u_value))
            and _write_thermal_transmittance(file, target, float(u_value))
        ):
            written += 1
    summary["psets_written"] = written
    return summary
