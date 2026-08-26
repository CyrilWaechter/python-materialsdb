"""Thermal construction composition: stack model, U-value math, IFC emission."""

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from materialsdb import cache, config, utils

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
        if lambda_value is None or not layer.thickness_m or layer.thickness_m <= 0:
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


def _has_identity_pset(file, material) -> bool:
    from materialsdb.ifc.material_builder import MATERIALSDB_PSET, _materials_of

    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != MATERIALSDB_PSET:
            continue
        if any(m.id() == material.id() for m in _materials_of(pset)):
            return True
    return False


def _purge_prior_layer_sets(file, name: str) -> None:
    """Remove prior IfcMaterialLayerSets called `name` before a re-append.

    Layers whose material carries a materialsdb identity pset are ours: their
    materials are purged outright. Foreign materials (no identity pset) are
    kept and merely detached from their layer references; emptied set shells
    are dropped so the replacement set stays unique."""
    from materialsdb.ifc.material_builder import purge_material

    stale = []
    for old_set in [s for s in file.by_type("IfcMaterialLayerSet") if s.LayerSetName == name]:
        for layer in list(old_set.MaterialLayers or ()):
            material = getattr(layer, "Material", None)
            if material is not None and _has_identity_pset(file, material):
                stale.append(material.id())
    for guid in dict.fromkeys(stale):
        purge_material(file, guid)
    # re-fetch: purge may already have removed some or all of the old sets
    for leftover in [s for s in file.by_type("IfcMaterialLayerSet") if s.LayerSetName == name]:
        for layer in list(leftover.MaterialLayers or ()):
            file.remove(layer)
        if not leftover.MaterialLayers:
            file.remove(leftover)


def to_ifc_layer_set(construction: Construction, store_, file=None):
    """Emit a wrapper IFC library containing the construction as an
    IfcMaterialLayerSet. Referenced materials are built through
    MaterialBuilder (single representative variant) so their identity psets
    ride along; IfcMaterialLayer.Description carries the source material guid.

    Appending into an existing session file replaces any prior layer set with
    the same name (materials matched by their materialsdb identity)."""
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
        # purge before building: a later find_existing must not re-attach to
        # materials that are about to be removed with the superseded set
        _purge_prior_layer_sets(target_file, construction.name)

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


def constructions_dir() -> Path:
    directory = cache.get_cache_folder() / "constructions"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "construction"


def _unique_path(directory: Path, base: str) -> Path:
    candidate = directory / f"{base}.json"
    index = 2
    while candidate.exists():
        candidate = directory / f"{base}-{index}.json"
        index += 1
    return candidate


def _find_stored_name_file(directory: Path, name: str) -> Path | None:
    for file in directory.glob("*.json"):
        data = json.loads(file.read_text(encoding="utf-8"))
        if data.get("name") == name:
            return file
    return None


def validate_construction(body: dict, store_) -> tuple[Construction, list[str]]:
    problems: list[str] = []
    name = str(body.get("name") or "").strip()
    if not name:
        problems.append("name required")
    layers_body = body.get("layers")
    if not isinstance(layers_body, list) or not layers_body:
        problems.append("at least one layer required")
        layers_body = []
    layers = []
    for index, entry in enumerate(layers_body):
        if not isinstance(entry, dict):
            problems.append(f"layer {index}: invalid entry")
            continue
        material_id = str(entry.get("material_id") or "")
        try:
            thickness = float(entry.get("thickness_m"))
        except (TypeError, ValueError):
            problems.append(f"layer {index}: thickness must be a number")
            continue
        if not math.isfinite(thickness) or thickness <= 0:
            problems.append(f"layer {index}: thickness must be > 0")
            continue
        if store_.get(material_id) is None:
            problems.append(f"unknown material id: {material_id}")
            continue
        layers.append(ConstructionLayer(material_id=material_id, thickness_m=thickness))
    design_usage = body.get("design_usage") or None
    if design_usage not in (None, *_DESIGN_USAGE_TO_DIRECTION):
        problems.append(f"invalid design_usage: {design_usage}")
    return Construction(name=name, design_usage=design_usage, layers=layers), problems


def _to_body(construction: Construction) -> dict:
    return {
        "name": construction.name,
        "design_usage": construction.design_usage,
        "layers": [
            {"material_id": layer.material_id, "thickness_m": layer.thickness_m} for layer in construction.layers
        ],
    }


def save_construction(construction: Construction, store_) -> Path:
    _, problems = validate_construction(_to_body(construction), store_)
    if problems:
        raise ValueError("; ".join(problems))
    directory = constructions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    existing = _find_stored_name_file(directory, construction.name)
    path = existing if existing is not None else _unique_path(directory, slugify(construction.name))
    payload = {
        "name": construction.name,
        "design_usage": construction.design_usage,
        "layers": [
            {"material_id": layer.material_id, "thickness_m": layer.thickness_m} for layer in construction.layers
        ],
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def load_construction(name_or_slug: str, store_) -> Construction | None:
    directory = constructions_dir()
    candidates = [directory / f"{slugify(name_or_slug)}.json"]
    for file in directory.glob("*.json"):
        data = json.loads(file.read_text(encoding="utf-8"))
        if data.get("name") == name_or_slug:
            candidates.insert(0, file)
            break
    for file in candidates:
        if file.exists():
            data = json.loads(file.read_text(encoding="utf-8"))
            layers = [
                ConstructionLayer(material_id=l["material_id"], thickness_m=float(l["thickness_m"]))
                for l in data.get("layers", [])
            ]
            return Construction(name=data["name"], design_usage=data.get("design_usage"), layers=layers)
    return None


def list_constructions() -> list[str]:
    names = []
    for file in constructions_dir().glob("*.json"):
        try:
            names.append(json.loads(file.read_text(encoding="utf-8"))["name"])
        except (json.JSONDecodeError, KeyError):
            continue
    return sorted(names)


def delete_construction(name_or_slug: str) -> bool:
    directory = constructions_dir()
    named = _find_stored_name_file(directory, name_or_slug)
    file = named if named is not None else directory / f"{slugify(name_or_slug)}.json"
    if file.exists():
        file.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# NON-SPEC vendor content: some producer tools encode a layer stack inside the
# <construction> string body (format observed as:
#   001[HEADER;][PREFIX$]THICK@GUID(FLAGS);...
# ). This is NOT part of materialsdb103.xsd (which defines a plain string).
# The decoder below is best-effort and read-only: unknown tokens are preserved
# verbatim, unresolvable guids are flagged by the caller, never fatal.
# ---------------------------------------------------------------------------

_LEGACY_LAYER_RE = re.compile(r"(?:\d+:\d+\$)?([0-9.]+)@([0-9a-fA-F-]{36})(?:\(([^)]*)\))?")


def parse_legacy_stack(body: str) -> dict:
    """Best-effort decode of a vendor-specific construction stack string.

    Returns {"version": str, "variants": [{"header_raw": str,
    "layers": [{"guid", "thickness_m", "flags_raw"}]}], "raw": body}.
    Semantics of header numbers / flag letters are UNKNOWN and preserved
    verbatim."""
    body = body.strip()
    version = body[:3]
    variants = []
    for group in re.findall(r"\[([^\]]*)\]", body):
        header_raw = ""
        layers = []
        for segment in (s for s in group.split(";") if s.strip()):
            match = _LEGACY_LAYER_RE.search(segment)
            if match is None:
                # not a layer -> opaque header token (only valid before layers)
                if not layers:
                    header_raw = segment
                continue
            layers.append(
                {
                    "guid": match.group(2),
                    "thickness_m": float(match.group(1)),
                    "flags_raw": match.group(3) or "",
                }
            )
        variants.append({"header_raw": header_raw, "layers": layers})
    return {"version": version, "variants": variants, "raw": body}
