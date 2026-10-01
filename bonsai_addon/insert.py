"""Map resolved payloads onto an ifcopenshell file via ifcopenshell.api.

Pure ifcopenshell (no bpy/bonsai imports) so CI can test it directly.
api handles GlobalId/OwnerHistory and keeps entities schema-valid; kwargs
target the 0.9 api (add_layer takes no thickness — edit_layer sets it;
root.create_entity takes ifc_class=)."""

import math
import re

import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.representation

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


def _all_materials_by_id(file):
    """material_id -> every IfcMaterial carrying a matching materialsdb pset.

    A multi-layer material has one IfcMaterial per layer, so callers that must
    touch each of them (restyle) need the full list."""
    found: dict[str, list] = {}
    for material in file.by_type("IfcMaterial"):
        material_id = _material_id_of(material)
        if material_id is not None:
            found.setdefault(material_id, []).append(material)
    return found


def existing_materials_by_id(file):
    """material_id -> first IfcMaterial carrying a matching materialsdb pset."""
    return {material_id: materials[0] for material_id, materials in _all_materials_by_id(file).items()}


def apply_restyle(file, payload, summary=None) -> int:
    """Style-only refresh: never touches psets, identity, layers or thicknesses.

    Every IfcMaterial carrying the entry's material_id is styled (a multi-layer
    material has several). Materials with a third-party/manual style are kept
    and their id recorded in `summary["kept_styles"]`; ids with no material in
    the model land in `summary["missing"]`. Returns the number restyled."""
    if summary is None:
        summary = {}
    summary.setdefault("kept_styles", [])
    summary.setdefault("missing", [])
    swaps = summary.setdefault("style_swaps", [])
    by_id = _all_materials_by_id(file)
    restyled = 0
    for entry in payload.get("materials") or []:
        material_id = str(entry.get("material_id") or "")
        targets = by_id.get(material_id) or []
        if not targets:
            summary["missing"].append(material_id)
            continue
        for material in targets:
            if not _should_style(material):
                summary["kept_styles"].append(material_id)
                continue
            _apply_style(file, entry, material, swaps)
            restyled += 1
    return restyled


def _existing_keys(file):
    """(material_id, layer_id | None) -> IfcMaterial already present, from the
    materialsdb identity + org_layer psets."""
    found = {}
    for material in file.by_type("IfcMaterial"):
        material_id = _material_id_of(material)
        if material_id is not None:
            found.setdefault((material_id, _org_layer_id_of(material)), material)
    return found


def _fingerprint_of(material) -> str | None:
    return ifcopenshell.util.element.get_psets(material).get("materialsdb", {}).get("fingerprint")


def _write_fingerprint(file, material, identity) -> None:
    """Bookkeeping backfill for a legacy material: stamp the server's fingerprint
    (and scheme) onto its identity pset without touching anything else."""
    fingerprint = identity.get("fingerprint")
    if fingerprint is None:
        return
    found = ifcopenshell.util.element.get_pset(material, name="materialsdb")
    if found is None:
        pset = ifcopenshell.api.run("pset.add_pset", file, product=material, name="materialsdb")
    else:
        pset = file.by_id(found["id"])
    properties = {"fingerprint": file.create_entity("IfcText", str(fingerprint))}
    scheme = identity.get("fingerprint_scheme")
    if scheme is not None:
        properties["fingerprint_scheme"] = file.create_entity("IfcText", str(scheme))
    ifcopenshell.api.run("pset.edit_pset", file, pset=pset, properties=properties)


def _backfill_fingerprints(file, material_id, identity) -> None:
    """Legacy baseline: write the entry's fingerprint onto every model entity of
    the material that lacks one (bookkeeping only, no warning)."""
    if identity.get("fingerprint") is None:
        return
    for entities in _model_layers(file, material_id).values():
        for model_material, _layer in entities:
            if _fingerprint_of(model_material) is None:
                _write_fingerprint(file, model_material, identity)


def _purge_our_psets(file, material) -> None:
    """Remove the materialsdb psets (identity, org layer and resolved) so a
    refresh replaces their property values instead of layering new ones on top.
    Foreign psets are kept."""
    for pset in list(file.get_inverse(material)):
        if not pset.is_a("IfcMaterialProperties"):
            continue
        name = pset.Name or ""
        if name == "materialsdb" or name.startswith("materialsdb."):
            for prop in list(pset.Properties):
                file.remove(prop)
            file.remove(pset)


def _layer_of(file, material):
    for layer in file.by_type("IfcMaterialLayer"):
        if layer.Material is not None and layer.Material.id() == material.id():
            return layer
    return None


def _model_layers(file, material_id) -> dict[str, list[tuple]]:
    """IfcMaterial entity id (string) -> [(IfcMaterial, IfcMaterialLayer | None)].

    Keyed by entity id rather than the `materialsdb.org_layer` GUID so legacy
    materials with no psets stay addressable by the server's update mapping."""
    found: dict[str, list[tuple]] = {}
    for material in file.by_type("IfcMaterial"):
        if _material_id_of(material) != material_id:
            continue
        found.setdefault(str(material.id()), []).append((material, _layer_of(file, material)))
    return found


def _entry_layer_id(entry) -> str | None:
    if entry.get("layer_id"):
        return entry["layer_id"]
    return (entry.get("psets") or {}).get("materialsdb.org_layer", {}).get("layer_id")


def _entry_thickness(entry) -> float | None:
    org_layer = (entry.get("psets") or {}).get("materialsdb.org_layer") or {}
    thickness = org_layer.get("thick")
    if thickness is None:
        thickness = (entry.get("layer") or {}).get("thick_m")
    return float(thickness) if thickness is not None else None


def _refresh_material(file, entry, material, swaps=None) -> None:
    """Replace our psets/attributes on an existing IfcMaterial in place."""
    _purge_our_psets(file, material)
    _add_identity_pset(file, material, entry["identity"])
    _add_property_psets(file, material, entry.get("psets"))
    material.Name = str(entry["name"])
    material.Description = str(entry.get("description") or "")
    material.Category = str(entry.get("category") or "")
    if _should_style(material):
        _apply_style(file, entry, material, swaps)


def _refresh_layer(file, entry, layer) -> None:
    """Re-label the model layer and re-point its org_layer identity.

    `IfcMaterialLayer.LayerThickness` is the model's own dimension (the design
    thickness chosen in the construction maker) and is never changed here."""
    thickness = layer.LayerThickness or _entry_thickness(entry)
    layer.Name = f"{entry['name']} | {round(thickness * 1000)}mm" if thickness else str(entry["name"])
    layer.Description = str(_entry_layer_id(entry) or "")


def _apply_update(file, entry, material, layers_by_id, summary) -> bool:
    """Refresh the model layer(s) that map to this entry's new layer id.

    Only mappings whose new layer id equals the entry's own layer id can be
    applied here; a mapping targeting another entry's layer, or a model layer
    absent from `layers_by_id`, is recorded in `summary["update_missing"]`
    rather than dropped silently. Without `force`, a legacy entity with no
    fingerprint is backfilled and an entity whose fingerprint already matches is
    left alone; with `force` the data is refreshed regardless. Returns True when
    at least one entity was refreshed in place."""
    new_layer_id = _entry_layer_id(entry)
    new_fingerprint = entry["identity"].get("fingerprint")
    force = bool(entry.get("force"))
    refreshed = False
    for model_layer_id, mapped_new_id in (entry.get("update") or {}).items():
        if mapped_new_id != new_layer_id:
            if model_layer_id not in summary["update_missing"]:
                summary["update_missing"].append(model_layer_id)
            continue
        entities = layers_by_id.get(model_layer_id)
        if not entities:
            if model_layer_id not in summary["update_missing"]:
                summary["update_missing"].append(model_layer_id)
            continue
        for model_material, model_layer in entities:
            current = _fingerprint_of(model_material)
            if not force:
                if current is None:
                    _write_fingerprint(file, model_material, entry["identity"])
                    continue
                if current == new_fingerprint:
                    continue
            _refresh_material(file, entry, model_material, summary.setdefault("style_swaps", []))
            refreshed = True
            if model_layer is not None:
                _refresh_layer(file, entry, model_layer)
    return refreshed


def _retarget_associations(file, old_set, replacement_material, replacement_set=None) -> None:
    """Keep IfcRelAssociatesMaterial valid when the layer set it relates to is
    removed: RelatingMaterial is mandatory, so deleting the set would null it and
    corrupt the file. Point the relation at the replacement layer set when one
    exists, else at the replacement IfcMaterial."""
    target = replacement_set if replacement_set is not None else replacement_material
    for relation in file.by_type("IfcRelAssociatesMaterial"):
        relating = relation.RelatingMaterial
        if relating is not None and relating.id() == old_set.id():
            relation.RelatingMaterial = target


def _remove_layer(file, layer, replacement_material=None, replacement_set=None) -> None:
    """Remove an IfcMaterialLayer, dropping its layer set when it becomes empty
    (an IfcMaterialLayerSet with no members is schema-invalid). Any
    IfcRelAssociatesMaterial relating to a dropped set is retargeted (see
    `_retarget_associations`) rather than left dangling."""
    layer_sets = list(getattr(layer, "ToMaterialLayerSet", None) or ())
    file.remove(layer)
    for parent in layer_sets:
        if not parent.MaterialLayers:
            if replacement_material is not None:
                _retarget_associations(file, parent, replacement_material, replacement_set)
            file.remove(parent)


def _purge_material(file, material, keep_layers=False) -> None:
    """Remove one superseded IfcMaterial and everything the add-on created for
    it — a local, pure-ifcopenshell equivalent of
    material_builder.purge_material (the add-on must not import materialsdb).

    Property psets are removed child-first, the material's own styled
    representation is detached, and unless `keep_layers` its IfcMaterialLayer is
    removed (the layer set is dropped when emptied). Shared IfcSurfaceStyle
    entities are intentionally kept; the floating styled chains are swept so
    they do not accumulate."""
    target = material.id()
    for pset in list(file.get_inverse(material)):
        if pset.is_a("IfcMaterialProperties"):
            for prop in list(pset.Properties or ()):
                file.remove(prop)
            file.remove(pset)
    for representation in list(getattr(material, "HasRepresentation", None) or ()):
        file.remove(representation)
    if not keep_layers:
        for layer in list(file.by_type("IfcMaterialLayer")):
            if layer.Material is not None and layer.Material.id() == target:
                _remove_layer(file, layer)
    file.remove(material)
    orphans = {item.id() for item in file.by_type("IfcStyledItem") if item.Item is None and not item.StyledByItem}
    for representation in list(file.by_type("IfcStyledRepresentation")):
        items = representation.Items or ()
        if not items or all(item.id() in orphans for item in items):
            file.remove(representation)
    for item in list(file.by_type("IfcStyledItem")):
        if item.id() in orphans:
            file.remove(item)


def _build_replacement_material(file, entry, swaps=None):
    """Create the replacement IfcMaterial (attributes + identity/property psets +
    best-effort style) for a `mode == "replace"` entry."""
    material = ifcopenshell.api.run(
        "material.add_material",
        file,
        name=str(entry["name"]),
        category=str(entry.get("category") or ""),
        description=str(entry.get("description") or ""),
    )
    _add_identity_pset(file, material, entry["identity"])
    _add_property_psets(file, material, entry.get("psets"))
    _apply_style(file, entry, material, swaps)
    return material


def _retarget_layer(entry, layer, material) -> None:
    """Point a reused IfcMaterialLayer at the replacement, keeping the model
    layer's design thickness/position (the construction geometry is the model's,
    not the replacement material's) and refreshing its label/description. Only
    when the model layer has no thickness does the entry's supply one."""
    layer.Material = material
    thickness = layer.LayerThickness
    if not thickness:
        thickness = _entry_thickness(entry)
    if thickness is not None:
        layer.LayerThickness = thickness
        layer.Name = f"{entry['name']} | {round(thickness * 1000)}mm"
    else:
        layer.Name = str(entry["name"])
    layer.Description = str(_entry_layer_id(entry) or "")


def _replace_failure(material_id, layer_id) -> dict:
    """Structured record for a replacement that could not be applied, keeping
    both the superseded material and (when given) its model layer."""
    return {"material_id": material_id, "layer_id": layer_id}


def _apply_replace(file, entry, summary) -> None:
    """Replace the superseded model material/layer named by `entry["replaces"]`.

    Layer-level (a `layer_id` is given) swaps only that model layer's
    IfcMaterial. Whole-material (no `layer_id`) handles every model layer of the
    material: one IfcMaterialLayer is reused when the replacement entry carries
    a layer and the extras are removed. The superseded IfcMaterials and their
    psets/style/org_layer are removed; an IfcRelAssociatesMaterial caught by a
    dropped layer set is retargeted to the replacement (mandatory RelatingMaterial).
    A malformed `replaces`, a target that is no longer present, or a failure
    while applying is recorded in `summary["replace_failed"]` instead of
    raising."""
    failed = summary.setdefault("replace_failed", [])
    material_id = None
    layer_id = None
    try:
        replaces = entry.get("replaces") or {}
        material_id = replaces.get("material_id")
        layer_id = replaces.get("layer_id")
        targets = _model_layers(file, material_id)
        if layer_id is not None:
            pairs = list(targets.get(str(layer_id)) or [])
            if not pairs:
                # `layer_id` may be the materialsdb.org_layer GUID rather than
                # the model entity id used as the map key
                pairs = [pair for group in targets.values() for pair in group if _org_layer_id_of(pair[0]) == layer_id]
        else:
            pairs = [pair for group in targets.values() for pair in group]
        if not pairs:
            failed.append(_replace_failure(material_id, layer_id))
            return
        material = _build_replacement_material(file, entry, summary.setdefault("style_swaps", []))
        if layer_id is not None:
            model_material, model_layer = pairs[0]
            if model_layer is not None:
                _retarget_layer(entry, model_layer, material)
            _purge_material(file, model_material, keep_layers=True)
            return
        keep = next((pair for pair in pairs if pair[1] is not None), None) if entry.get("layer") else None
        replacement_set = None
        if keep is not None:
            layer_sets = list(getattr(keep[1], "ToMaterialLayerSet", None) or ())
            replacement_set = layer_sets[0] if layer_sets else None
        keep_ids = (keep[0].id(), keep[1].id()) if keep is not None else None
        for model_material, model_layer in pairs:
            if model_layer is None:
                continue
            if (model_material.id(), model_layer.id()) == keep_ids:
                continue
            _remove_layer(file, model_layer, material, replacement_set)
        if keep is not None:
            _retarget_layer(entry, keep[1], material)
        for model_material, _model_layer in pairs:
            _purge_material(file, model_material, keep_layers=True)
    except Exception:  # noqa: BLE001 - a failed replacement must not abort the push
        failed.append(_replace_failure(material_id, layer_id))


def _add_identity_pset(file, material, identity):
    pset = ifcopenshell.api.run("pset.add_pset", file, product=material, name="materialsdb")
    properties = {
        "material_id": identity["material_id"],
        "company_id": identity.get("company_id") or "",
        "company": identity.get("company") or "",
    }
    fingerprint = identity.get("fingerprint")
    if fingerprint is not None:
        properties["fingerprint"] = file.create_entity("IfcText", str(fingerprint))
        scheme = identity.get("fingerprint_scheme")
        if scheme is not None:
            properties["fingerprint_scheme"] = file.create_entity("IfcText", str(scheme))
    ifcopenshell.api.run("pset.edit_pset", file, pset=pset, properties=properties)


def _add_property_psets(file, material, psets):
    for pset_name, props in (psets or {}).items():
        pset = ifcopenshell.api.run("pset.add_pset", file, product=material, name=str(pset_name))
        ifcopenshell.api.run("pset.edit_pset", file, pset=pset, properties=props)


_OUR_STYLE_NAME = re.compile(r"^(?:color \d+|category .+)$")


def _resolved_style(material_entry):
    """(name, (r, g, b) in 0..1) for a material entry.

    Prefers the server-resolved `style_name`/`style_color` (producer colour or
    the active scheme's category colour); falls back to the legacy producer
    `color` for payloads produced by an older server."""
    name = material_entry.get("style_name")
    rgb = material_entry.get("style_color")
    if name and rgb:
        return name, tuple(float(component) for component in rgb)
    color = material_entry.get("color")
    if not color:
        return None
    color = int(color)
    return f"color {color}", (((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255)


def _valid_style_entities(material):
    """The valid (non-empty) IfcSurfaceStyle entities assigned to a material."""
    found = []
    for representation in material.HasRepresentation or ():
        for styled in representation.Representations or ():
            for item in styled.Items or ():
                if not item.is_a("IfcStyledItem"):
                    continue
                for style in item.Styles or ():
                    if style.is_a("IfcSurfaceStyle") and style.Styles:
                        found.append(style)
                    elif style.is_a("IfcPresentationStyleAssignment"):
                        found.extend(
                            nested for nested in style.Styles or () if nested.is_a("IfcSurfaceStyle") and nested.Styles
                        )
    return found


def _valid_style_names(material):
    """Names of the valid (non-empty) surface styles assigned to a material."""
    return [style.Name for style in _valid_style_entities(material)]


def _style_in_use(file, style) -> bool:
    """True when any IfcMaterial still carries `style`."""
    return any(style in _valid_style_entities(material) for material in file.by_type("IfcMaterial"))


def _should_style(material):
    """True when a material has no valid style, or only styles we created (so
    the materialsdb colour may be refreshed); a third-party style is kept."""
    names = _valid_style_names(material)
    if not names:
        return True
    return all(name and _OUR_STYLE_NAME.match(name) for name in names)


def _apply_style(file, material_entry, material, swaps=None):
    """Best-effort schema-valid material styling; needs a representation
    context (bonsai files have one, scratch CI files do not).

    The IfcSurfaceStyle is built in one shot: creating it via
    style.add_style leaves Styles=$ (invalid, SET [1:?]) until
    add_surface_style runs, so a mid-way failure would persist an entity
    that crashes ifcopenshell's style loader on the next open. Existing
    styles with NULL/empty Styles are ignored on reuse for the same
    reason.

    `swaps` optionally collects ``(superseded_style, new_style)`` entity pairs:
    switching a material to a DIFFERENT style entity leaves already-loaded
    Blender objects on the old style's material (Bonsai copies it into mesh
    slots at load time), so the Blender layer repoints them after the push."""
    resolved = _resolved_style(material_entry)
    if resolved is None:
        return
    name, rgb = resolved
    old_styles = _valid_style_entities(material)
    try:
        context = ifcopenshell.util.representation.get_context(file, "Model", "Body", "MODEL_VIEW")
        if context is None:
            context = ifcopenshell.util.representation.get_context(file, "Model")
        if context is None:
            contexts = file.by_type("IfcRepresentationContext")
            if not contexts:
                return
            context = contexts[0]
        styles = {}
        for style in file.by_type("IfcSurfaceStyle"):
            if style.Styles:  # never reuse a malformed (NULL/empty Styles) style
                styles.setdefault(style.Name, style)
        style = styles.get(name)
        if style is None:
            shading = file.create_entity(
                "IfcSurfaceStyleShading",
                SurfaceColour=file.create_entity("IfcColourRgb", Name=None, Red=rgb[0], Green=rgb[1], Blue=rgb[2]),
            )
            style = file.create_entity("IfcSurfaceStyle", Name=name, Side="BOTH", Styles=[shading])
        else:
            # reused style: recolour only when the requested RGB actually
            # differs (0-255 comparison, no false writes from float rounding);
            # shadings without a SurfaceColour are unknown structures -> untouched
            wanted = tuple(round(component * 255) for component in rgb)
            for item in style.Styles or ():
                if not item.is_a("IfcSurfaceStyleShading"):
                    continue
                colour = item.SurfaceColour
                if colour is None:
                    continue
                current = (round(colour.Red * 255), round(colour.Green * 255), round(colour.Blue * 255))
                if current != wanted:
                    colour.Red, colour.Green, colour.Blue = rgb[0], rgb[1], rgb[2]
        ifcopenshell.api.run("style.assign_material_style", file, material=material, style=style, context=context)
        if swaps is not None and style not in old_styles:
            for old_style in old_styles:
                if not _style_in_use(file, old_style):
                    swaps.append((old_style, style))
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


def apply_add_materials(file, payload, summary=None) -> int:
    """Create IfcMaterial (+ identity/property psets, layer + set, style) per
    payload entry. Entries whose (material_id, layer_id) key already exists are
    re-styled when their colour is ours or missing. An existing material whose
    stored fingerprint differs from the entry's is left alone (mode "skip") or
    refreshed in place per the entry's `update` mapping (mode "update"); a
    legacy material without a fingerprint gets the baseline stamped on it.
    Returns the number created. When a `summary` dict is passed it is populated
    with `changed_skipped`/`update_missing` (the picker's changed material ids
    and any update mappings that could not be applied) and `replace_failed`
    (replacements whose target was no longer present). A `mode == "replace"`
    entry supersedes the material/layer named by its `replaces` mapping: the
    superseded entities are removed and the entry's material takes their
    place."""
    existing = _existing_keys(file)
    known_by_id = existing_materials_by_id(file)
    created = 0
    if summary is None:
        summary = {}
    summary.setdefault("update_missing", [])
    summary.setdefault("changed_skipped", [])
    summary.setdefault("replace_failed", [])
    swaps = summary.setdefault("style_swaps", [])
    for entry in payload.get("materials") or []:
        identity = entry["identity"]
        material_id = identity["material_id"]
        mode = entry.get("mode", "skip")
        if mode == "replace":
            _apply_replace(file, entry, summary)
            existing = _existing_keys(file)
            known_by_id = existing_materials_by_id(file)
            continue
        layer = entry.get("layer") or {}
        org_layer_id = (entry.get("psets") or {}).get("materialsdb.org_layer", {}).get("layer_id")
        key = (material_id, layer.get("layer_id") or org_layer_id)
        current = known_by_id.get(material_id)
        if current is not None:
            if mode == "update":
                _apply_update(file, entry, current, _model_layers(file, material_id), summary)
                continue
            current_fingerprint = _fingerprint_of(current)
            new_fingerprint = identity.get("fingerprint")
            if current_fingerprint is None:
                _backfill_fingerprints(file, material_id, identity)
            elif new_fingerprint is not None and current_fingerprint != new_fingerprint:
                if material_id not in summary["changed_skipped"]:
                    summary["changed_skipped"].append(material_id)
                continue
        if key in existing:
            if _should_style(existing[key]):
                _apply_style(file, entry, existing[key], swaps)
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
        _apply_style(file, entry, material, swaps)
        existing[key] = material
        known_by_id.setdefault(material_id, material)
        created += 1
    return created


def apply_add_construction(file, payload) -> dict:
    """Upsert a construction as typed element(s) + IfcMaterialLayerSet.

    Types are created if missing and kept if present; a re-send rebuilds the
    existing set IN PLACE (same entity, old layers removed) and re-assigns
    all targets onto it. Generic usage shares ONE set across all types via a
    single IfcRelAssociatesMaterial. Materials are found or created by their
    `(material_id, materialsdb.org_layer.layer_id)` key, exactly like the
    picker flow, so each construction layer owns the entity for its own
    resolved layer (identity + resolved psets + style on creation). Placeholder
    layers (material_id None) re-attach a model material
    by name or create one, carrying a Pset_MaterialThermal when a λ is
    given. A `u_values` map (per type class) writes `ThermalTransmittance`
    into the matching `Pset_<Type>Common` pset, created or edited in place."""
    construction = payload["construction"]
    summary = {
        "types_created": 0,
        "sets_updated": 0,
        "materials_created": 0,
        "placeholders_matched": 0,
        "update_missing": [],
        "changed_skipped": [],
        "replace_failed": [],
        "style_swaps": [],
    }

    known = _existing_keys(file)
    known_by_id = existing_materials_by_id(file)
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
        entry = layer["material"]
        identity = entry["identity"]
        material_id = identity["material_id"]
        mode = entry.get("mode", "skip")
        org_layer_id = (entry.get("psets") or {}).get("materialsdb.org_layer", {}).get("layer_id")
        key = (material_id, org_layer_id)
        material = known.get(key)
        current = known_by_id.get(material_id)
        changed = False
        refreshed = False
        if mode == "replace":
            _apply_replace(file, entry, summary)
            known = _existing_keys(file)
            known_by_id = existing_materials_by_id(file)
            material = known.get(key)
        elif current is not None:
            if mode == "update":
                # _refresh_material already applied the style, so skip the
                # generic re-style below when it refreshed in place
                refreshed = _apply_update(file, entry, current, _model_layers(file, material_id), summary)
                if material is None:
                    material = current
            else:
                current_fingerprint = _fingerprint_of(current)
                new_fingerprint = identity.get("fingerprint")
                if current_fingerprint is None:
                    _backfill_fingerprints(file, material_id, identity)
                elif new_fingerprint is not None and current_fingerprint != new_fingerprint:
                    if material_id not in summary["changed_skipped"]:
                        summary["changed_skipped"].append(material_id)
                    changed = True
                    if material is None:
                        material = current
        if material is None:
            material = ifcopenshell.api.run(
                "material.add_material",
                file,
                name=str(entry["name"]),
                category=str(entry.get("category") or ""),
                description=str(entry.get("description") or ""),
            )
            _add_identity_pset(file, material, entry["identity"])
            _add_property_psets(file, material, entry.get("psets"))
            known[key] = material
            known_by_id.setdefault(material_id, material)
            summary["materials_created"] += 1
        # style reused materials too: an earlier push (or a pre-fix session)
        # may have left them colourless, and users expect the colour to show.
        # A changed "skip" leaves the material untouched entirely, and an
        # in-place update already refreshed its style in _refresh_material.
        if not changed and not refreshed and _should_style(material):
            _apply_style(file, entry, material, summary["style_swaps"])
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
