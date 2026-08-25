# Design: Construction maker v1

Date: 2026-08-25
Status: Approved (brainstorming session, sub-project 5)

## Goals

Compose thermal constructions from materialsdb materials: ordered layers of a
material + thickness, live U-value per recognized standards, export as
IfcMaterialLayerSet. A core Python module carries the physics; the picker's web
UI gains a constructions section.

Non-goals (this round): importing the 43 non-spec encoded `<construction>`
strings (vendor-specific content outside the XSD — the XSD defines only a
plain string plus consref/designusage); IfcMaterialLayerSetUsage and layer
placement geometry; sharing constructions between users.

## Decisions

- Approach A: core module + GUI section + JSON file library (one file per
  construction under `<cache>/constructions/`).
- U-value per ISO 6946 structure with an extensible resistance-preset
  registry; presets are plain data so adding SIA 180 or country variants is a
  dict entry. Exact preset numbers are verified against each standard during
  implementation and flagged for maintainer confirmation.
- Layer order convention: index 0 = exterior side.
- Thickness canonical unit: meters in the model/API, millimetres in the UI.

## Core (`src/materialsdb/construction.py`)

```python
@dataclass
class ConstructionLayer:
    material_id: str      # simple-type materialsdb material guid
    thickness_m: float

@dataclass
class Construction:
    name: str
    design_usage: str | None   # consDesignForWall | consDesignForRoof | consDesignForFloor
    layers: list[ConstructionLayer]   # [0] = exterior

RESISTANCE_PRESETS = {
    "ISO6946": {"wall": (0.13, 0.04), "roof": (0.10, 0.04),
                "floor": (0.17, 0.04), "generic": (0.13, 0.04)},
    "SIA180":  {...},   # verified during implementation; maintainer-confirmed
}

def u_value(construction, store_, preset="ISO6946") -> UResult
    # UResult(u, contributions=[{material_id, name, d_m, lambda_value, r}],
    #         missing_lambda_ids)
    # lambda resolution: first country-resolved lambda_value on the referenced
    # material's layers; layers without lambda excluded from the sum + flagged.

def to_ifc_layer_set(construction, store_, file=None) -> ifcopenshell.file
    # wrapper library; IfcMaterialLayerSet with IfcMaterialLayers carrying the
    # CONSTRUCTION thicknesses (overriding source geometry); referenced
    # IfcMaterials created via MaterialBuilder -> identity psets ride along
```

## Persistence

`<cache>/constructions/<slug(name)>.json`:

```json
{"name": "...", "design_usage": "consDesignForWall",
 "layers": [{"material_id": "...", "thickness_m": 0.2}],
 "preset": "ISO6946", "created": "<iso8601>"}
```

Save validates material ids against the store (unknown ids rejected with the
offender list) and thickness > 0.

## GUI

New `static/constructions.html` + `app-constructions.js`, linked from the
picker page. Reuses token/session machinery.

Layout: saved-constructions list (left), layer table (center: reorder up/down,
material name resolved via display_name lookup, thickness input mm, lambda,
remove; "add layer" opens a search modal over `/api/materials`), U-value card
(preset selector, designUsage selector, per-layer r contributions,
missing-lambda warnings). Actions: save / delete / export standalone .ifc /
append into open session.

New API:

| Endpoint | Purpose |
|---|---|
| `GET /api/constructions` | list saved names |
| `GET /api/constructions/{name}` | one construction |
| `POST /api/constructions/{name}` | create/overwrite (validated body) |
| `DELETE /api/constructions/{name}` | remove file |
| `POST /api/u_value` `{construction, preset}` | computed UResult |

All mutating endpoints keep the existing token gate.

## Error handling

Unknown material ids at save → error listing offenders. Non-positive or
non-numeric thickness → rejected. Missing lambda layers → flagged in UResult
and rendered as warnings, never fatal. Duplicate construction names overwrite
only via explicit save to that name; slug collisions get `-2` suffixes.

## Testing

Known-answer U math (0.2 m @ 0.036 → R = 5.5556 before resistances); preset
registry behaviour incl. designUsage selection; missing-lambda flagging;
to_ifc roundtrip (reopen: layer count, order, thicknesses, identity psets);
CRUD endpoints incl. slug safety and validation failures; frontend manual
checklist (JS untested by automation, consistent with project practice).
