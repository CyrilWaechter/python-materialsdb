"""Named colour palettes ("schemes") with import/export and persistence.

The built-in palette is the Lesosai scheme from the materialsdb standard. User
schemes live as JSON in the per-user config directory. Reads never raise: a
damaged file only disables custom palettes and reports through ``load_error()``.
"""

import json
import os
from pathlib import Path
from typing import Any, TypedDict, cast

from materialsdb import config

FORMAT = "materialsdb-scheme/1"


class SchemeError(ValueError):
    """Invalid name, palette, envelope or unknown scheme operation."""


class CategoryStyle(TypedDict):
    """One palette entry. `hatch` is reserved for future pattern support."""

    hatch: str
    color: tuple[int, int, int]


CATEGORIES: dict[str, CategoryStyle] = {
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

DEFAULT_SCHEME = "Lesosai"
BUILTIN: dict[str, dict[str, CategoryStyle]] = {DEFAULT_SCHEME: CATEGORIES}
RESERVED = frozenset({*BUILTIN, "import"})

_last_error: str | None = None


def schemes_path() -> Path:
    """Path of the user's schemes.json, resolved lazily on every call."""
    return config.get_config_dir() / "schemes.json"


def load_error() -> str | None:
    """Message produced by the last damaged schemes.json read, else None."""
    return _last_error


def _validate_name(name: object) -> None:
    if not isinstance(name, str) or not name:
        raise SchemeError("scheme name must be a non-empty string")
    if len(name) > 64:
        raise SchemeError("scheme name must be at most 64 characters")
    if "/" in name or any(ord(char) < 32 for char in name):
        raise SchemeError("scheme name must not contain '/' or control characters")
    if name in RESERVED:
        raise SchemeError(f"scheme name {name!r} is reserved")


def validate(name: str, categories: Any) -> None:
    """Raise SchemeError unless `categories` is a complete valid palette."""
    _validate_name(name)
    if not isinstance(categories, dict) or set(categories) != set(CATEGORIES):
        raise SchemeError(f"a palette must define exactly these {len(CATEGORIES)} categories: {set(CATEGORIES)}")
    for category, style in categories.items():
        if not isinstance(style, dict):
            raise SchemeError(f"style for category {category!r} must be a mapping")
        if not isinstance(style.get("hatch", ""), str):
            raise SchemeError(f"hatch for category {category!r} must be a string")
        color = style.get("color")
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise SchemeError(f"color for category {category!r} must have 3 components")
        for component in color:
            if isinstance(component, bool) or not isinstance(component, int):
                raise SchemeError(f"color components for category {category!r} must be integers")
            if not 0 <= component <= 255:
                raise SchemeError(f"color components for category {category!r} must be in 0..255")


def _normalise(categories: dict[str, Any]) -> dict[str, CategoryStyle]:
    """Validated palette -> JSON-ready entries (lists instead of tuples)."""
    return cast(
        dict[str, CategoryStyle],
        {
            category: {"hatch": style.get("hatch") or "", "color": [int(component) for component in style["color"]]}
            for category, style in categories.items()
        },
    )


def _load_custom() -> tuple[dict[str, dict[str, CategoryStyle]], str | None]:
    """Read custom palettes; never raise, return ({}, error message) on damage."""
    global _last_error
    path = schemes_path()
    if not path.exists():
        _last_error = None
        return {}, None
    try:
        stored = json.loads(path.read_text("utf-8"))["schemes"]
        if not isinstance(stored, dict):
            raise SchemeError("'schemes' must be an object")
        custom: dict[str, dict[str, CategoryStyle]] = {}
        for name, categories in stored.items():
            validate(name, categories)
            custom[name] = _normalise(categories)
        _last_error = None
        return custom, None
    except Exception as err:  # noqa: BLE001 - a damaged file must never break callers
        _last_error = f"schemes.json ignored: {err}"
        return {}, _last_error


def _write(custom: dict[str, dict[str, CategoryStyle]]) -> None:
    path = schemes_path()
    tmp = path.with_name("schemes.json.tmp")
    tmp.write_text(json.dumps({"version": 1, "schemes": custom}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def available() -> dict[str, dict[str, CategoryStyle]]:
    """Built-in palettes first, then custom ones sorted by name."""
    custom, _ = _load_custom()
    return {**BUILTIN, **dict(sorted(custom.items()))}


def get(name: str) -> dict[str, CategoryStyle] | None:
    """Palette registered under `name`, or None if unknown."""
    return available().get(name)


def save(name: str, categories: Any) -> None:
    """Validate and persist a custom palette (overwrites an existing name)."""
    validate(name, categories)
    custom, _ = _load_custom()
    custom[name] = _normalise(categories)
    _write(custom)


def rename(old: str, new: str) -> None:
    """Rename a custom palette; built-in/reserved/used target names are refused."""
    custom, _ = _load_custom()
    if old not in custom:
        raise SchemeError(f"no custom scheme named {old!r}")
    new = new.strip()
    _validate_name(new)
    if new in custom:
        raise SchemeError(f"a custom scheme named {new!r} already exists")
    custom[new] = custom.pop(old)
    _write(custom)


def delete(name: str) -> None:
    """Delete a custom palette; built-in palettes cannot be deleted."""
    custom, _ = _load_custom()
    if name not in custom:
        raise SchemeError(f"no custom scheme named {name!r}")
    del custom[name]
    _write(custom)


def export_payload(name: str) -> dict[str, Any]:
    """Self-describing envelope suitable for json.dumps and later import."""
    palette = get(name)
    if palette is None:
        raise SchemeError(f"unknown scheme {name!r}")
    return {"format": FORMAT, "name": name, "categories": palette}


def import_payload(data: Any) -> str:
    """Validate and save an exported envelope, returning the scheme name."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise SchemeError(f"expected a {FORMAT} payload")
    name = data.get("name")
    categories = data.get("categories")
    validate(name, categories)
    if name in available():
        raise SchemeError(f"a scheme named {name!r} already exists")
    save(name, categories)
    return name
