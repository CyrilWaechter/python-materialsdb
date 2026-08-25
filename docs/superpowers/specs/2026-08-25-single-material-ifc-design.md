# Design: Single-material IFC creation

Date: 2026-08-25
Status: Approved (brainstorming session, sub-project 3 of 5)

## Goals

1. Create one material's IFC representation on demand: appended into an
   existing ifcopenshell file (Bonsai/FreeCAD/Revit plugin use case) or as a
   minimal standalone file.
2. Idempotent appends keyed by materialsdb identity, so GUI re-selections never
   duplicate entities.
3. One shared code path for batch (per-supplier) export and single export.

Non-goals: placing usage types (IfcWallType etc.) into foreign projects, any
GUI work, changes to query/store layers.

## Decisions (from session)

- Destination: both append-into-existing-file and standalone; append-first.
- Entity scope: IfcMaterial + property sets + surface style + layer set when a
  thickness exists. NO usage type entities in the single-material path.
- Identity: a small `materialsdb` IfcMaterialProperties pset (id, company_id,
  company, verXML) written on every created material; append reuses an existing
  material whose pset id matches instead of duplicating.
- Approach A: extract a shared MaterialBuilder; ProjectLibrary delegates to it;
  the O(n^2) surface-style lookup becomes a name-keyed cache en route.

## Architecture

New module `src/materialsdb/ifc/material_builder.py`:

```python
class MaterialBuilder:
    def __init__(self, file, country=None, lang=None): ...
        # country/lang default to config like the rest of the library
    def build(self, material, company_id="", company=""):
        # IfcMaterial(Name, Description, Category)
        # 'materialsdb' pset: id, company_id, company, verXML
        # PSETS-driven psets (country-aware values, primary_measure_type filter)
        # IfcSurfaceStyle from color/category via name-keyed cache
        # IfcMaterialLayer/Set when geometry.thick exists for country

    def find_existing(self, material_id):
        # scan file.by_type("IfcMaterialProperties") where Name == "materialsdb"
        # compare Properties id -> return owning IfcMaterial or None


def add_material(file, material, company_id="", company="", replace=False):
    # find_existing: return as-is; replace=True -> delete found set, rebuild


def create_material_file(material_id):
    # store.get(material_id) -> KeyError if unknown
    # new ifcopenshell.file(schema="IFC4") + application/owner stub +
    # IfcProjectLibrary wrapper (reuses ProjectLibrary.create_project_library)
    # + add_material
```

Moved from `project_library.py` into the new module (imported back there for
compatibility): `CATEGORIES`, `PSETS`, `clean_psets`, `get_value`.

Refactor: `ProjectLibrary.create_materials` per-material body delegates to
`MaterialBuilder.build`; usage-type entity creation stays in project_library
(batch-only behavior). Surface style resolution uses the builder's cache, which
removes the current `file.by_type("IfcSurfaceStyle")` scans per material.

## Semantics

- Idempotency: key = `id` property of the `materialsdb` pset. Lookup is one
  `by_type("IfcMaterialProperties")` pass filtered by Name — bounded by target
  file size, no global scans.
- `replace=True`: removes the found material's owned entities (material, its
  psets, styles it uniquely owns are left shared) and rebuilds with fresh
  GlobalIds; identity (materialsdb id) unchanged.
- Standalone files reuse today's owner/application/IfcProjectLibrary wrapper so
  Bonsai and FreeCAD open them without repair prompts.
- Missing data skips that aspect silently (no thermal -> no thermal pset; no
  thickness for country -> no layer set), matching current batch behavior.
- Unknown material_id -> KeyError naming the id.

## Testing

Fixture-based via the committed `mini_producer.xml` through the store:

1. Standalone roundtrip: create_material_file(id).write -> reopen -> entities
   present, file opens without ifcopenshell errors.
2. Idempotency: add_material twice -> exactly one IfcMaterial for that id.
3. replace=True: same identity, fresh GlobalIds on rebuilt entities.
4. Batch equivalence: refactored supplier export produces the same entity
   inventory (counts by type + names/pset names) as the pre-refactor code on
   the mini fixture (golden snapshot captured before refactor).
5. Style-cache regression: creating N materials yields O(categories+colors)
   IfcSurfaceStyle entities, not O(N).
6. KeyError path for unknown id.

## Docs

README section "Create a single material in IFC" + new example script
`examples/create_single_material_ifc.py`.
