from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from materialsdb import config, utils
from materialsdb.classes import Material

USAGE_FLAGS = ("wall", "roof", "floor", "door")


@dataclass
class MaterialSummary:
    id: str
    company_id: str
    company: str
    category: str
    names: Dict[str, str]
    descriptions: Dict[str, str]
    lambda_min: Optional[float]
    lambda_max: Optional[float]
    thick_min: Optional[float]
    thick_max: Optional[float]
    usage: Dict[str, bool]


def _localized_dict(items) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in items or ():
        result[str(item.lang or "")] = str(item)
    return result


def _min_max(values) -> Tuple[Optional[float], Optional[float]]:
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return min(values), max(values)


def summarize_material(
    material: Material,
    company_id: str = "",
    company: str = "",
    country: Optional[str] = None,
) -> MaterialSummary:
    country = country or config.get_country()
    information = material.information

    lambdas = []
    thicks = []
    for layer in utils.get_material_layers(material):
        thermal = utils.get_by_country(layer.thermal or (), country)
        geometry = utils.get_by_country(layer.geometry or (), country)
        if thermal is not None:
            lambdas.append(thermal.lambda_value)
        if geometry is not None:
            thicks.append(geometry.thick)

    lambda_min, lambda_max = _min_max(lambdas)
    thick_min, thick_max = _min_max(thicks)

    return MaterialSummary(
        id=str(material.id),
        company_id=str(company_id),
        company=str(company),
        category=str(information.group or ""),
        names=_localized_dict(getattr(information.names, "name", ())),
        descriptions=_localized_dict(
            getattr(getattr(information, "explanations", None), "explanation", ())
        ),
        lambda_min=lambda_min,
        lambda_max=lambda_max,
        thick_min=thick_min,
        thick_max=thick_max,
        usage={flag: str(getattr(information, flag)) == "1" for flag in USAGE_FLAGS},
    )
