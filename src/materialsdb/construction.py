"""Thermal construction composition: stack model, U-value math, IFC emission."""

import math
from dataclasses import dataclass, field

from materialsdb import config, utils

# Surface resistances (m2K/W): interior/exterior by heat-flow direction.
# ISO 6946 table values; SIA 180 references the same table for these boundary
# cases (verified against secondary literature 2026-08). Kept as separate
# entries so standard-specific corrections have an explicit home.
RESISTANCE_PRESETS = {
    "ISO6946": {
        "wall": (0.13, 0.04),
        "roof": (0.10, 0.04),
        "floor": (0.17, 0.04),
        "generic": (0.13, 0.04),
    },
    "SIA180": {
        "wall": (0.13, 0.04),
        "roof": (0.10, 0.04),
        "floor": (0.17, 0.04),
        "generic": (0.13, 0.04),
    },
}

_DESIGN_USAGE_TO_DIRECTION = {
    "consDesignForWall": "wall",
    "consDesignForRoof": "roof",
    "consDesignForFloor": "floor",
}


@dataclass
class ConstructionLayer:
    material_id: str
    thickness_m: float


@dataclass
class Construction:
    name: str
    design_usage: str | None
    layers: list[ConstructionLayer] = field(default_factory=list)


@dataclass
class UResult:
    u: float | None
    rsi: float
    rse: float
    contributions: list[dict]
    missing_lambda_ids: list[str]


def resolve_lambda(store_, material_id: str, country: str | None = None) -> float | None:
    """First country-resolved lambda_value across the material's layers."""
    material = store_.get(material_id)
    if material is None:
        return None
    country = country or config.get_country()
    for layer in getattr(getattr(material, "layers", None), "layer", ()) or ():
        thermal = utils.get_by_country(layer.thermal or (), country)
        if thermal is not None and thermal.lambda_value is not None:
            return thermal.lambda_value
    return None


def _direction(design_usage: str | None) -> str:
    return _DESIGN_USAGE_TO_DIRECTION.get(design_usage or "", "generic")


def u_value(construction: Construction, store_, preset: str = "ISO6946") -> UResult:
    if preset not in RESISTANCE_PRESETS:
        raise ValueError(f"unknown preset: {preset} (available: {sorted(RESISTANCE_PRESETS)})")
    direction = _direction(construction.design_usage)
    rsi, rse = RESISTANCE_PRESETS[preset][direction]
    country = config.get_country()

    contributions = []
    missing = []
    r_sum = 0.0
    for layer in construction.layers:
        name = ""
        summary = store_.get_summary(layer.material_id)
        if summary is not None:
            name = summary.names.get(config.get_lang()) or summary.names.get("") or ""
        lambda_value = resolve_lambda(store_, layer.material_id, country)
        if lambda_value is None or not layer.thickness_m:
            missing.append(layer.material_id)
            continue
        r_layer = layer.thickness_m / lambda_value
        r_sum += r_layer
        contributions.append(
            {
                "material_id": layer.material_id,
                "name": name,
                "d_m": layer.thickness_m,
                "lambda_value": lambda_value,
                "r": r_layer,
            }
        )

    if missing or not contributions or math.isclose(r_sum, 0):
        return UResult(u=None, rsi=rsi, rse=rse, contributions=contributions, missing_lambda_ids=missing)

    u = 1 / (rsi + r_sum + rse)
    return UResult(u=u, rsi=rsi, rse=rse, contributions=contributions, missing_lambda_ids=missing)


def to_ifc_layer_set(construction: Construction, store_, file=None):
    """Emit a wrapper IFC library containing the construction as an
    IfcMaterialLayerSet. Referenced materials are built through
    MaterialBuilder (single representative variant) so their identity psets
    ride along; IfcMaterialLayer.Description carries the source material guid."""
    import uuid

    from materialsdb.ifc.material_builder import MaterialBuilder
    from materialsdb.ifc.project_library import ProjectLibrary

    missing = [layer.material_id for layer in construction.layers if store_.get(layer.material_id) is None]
    if missing:
        raise ValueError(f"unknown material ids: {', '.join(missing)}")

    if file is None:
        library = ProjectLibrary()
        library.create_project_library(
            company="MaterialsDB Constructions",
            companyid=str(uuid.uuid4()),
            ver=1,
            crd=utils.new_tdatetime(),
        )
        target_file = library.file
    else:
        library = None
        target_file = file

    builder = MaterialBuilder(target_file)
    material_layers = []
    for layer in construction.layers:
        summary = store_.get_summary(layer.material_id)
        material = store_.get(layer.material_id)
        created = builder.build(
            material,
            company_id=str(summary.company_id),
            company=summary.company,
            with_layers=False,
        )
        assert len(created) == 1, "with_layers=False must yield exactly one IfcMaterial"
        name = summary.names.get(config.get_lang()) or ""
        element_name = f"{name} | {round(layer.thickness_m * 1000)} mm"
        ifc_layer = target_file.create_entity(
            "IfcMaterialLayer",
            Material=created[0],
            LayerThickness=layer.thickness_m,
            Description=layer.material_id,
            Name=element_name,
        )
        material_layers.append(ifc_layer)

    target_file.create_entity(
        "IfcMaterialLayerSet",
        MaterialLayers=material_layers,
        LayerSetName=construction.name,
    )
    return target_file if library is None else library.file
