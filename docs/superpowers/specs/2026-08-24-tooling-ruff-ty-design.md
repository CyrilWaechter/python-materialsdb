# Design: Tooling migration — ruff, ty, packaging consolidation, CI

Date: 2026-08-24
Status: Approved (brainstorming session, sub-project 1 of 5)

## Goals

1. Replace black with ruff for formatting and linting, line-length 120.
2. Adopt ty for type checking.
3. Consolidate packaging metadata into pyproject.toml (delete setup.cfg).
4. Add a GitHub Actions CI workflow running lint, types, format check, tests.

## Decisions

- Python floor: `requires-python = ">=3.10"` — unlocks PEP 604/585 annotation
  modernization (`X | None`, builtin generics), which is ~283 of the current
  362 lint findings. The library calls `typing.get_type_hints()` at runtime,
  so modern syntax must be evaluated at runtime; 3.10 is the safe floor.
  Matches modern BIM hosts (Blender 4.x = py3.11, FreeCAD 1.0 = py3.11).
- Lint rules: ruff 0.16 new-default rule set (413 rules), zero-error end
  state enforced by CI. Baseline today: 362 findings, 320 auto-fixable.
- Generated code policy: `src/materialsdb/classes.py` is excluded from both
  `ruff check` and `ruff format` (it is produced by
  `dev_utils/classes_generator.py`; formatter/lint edits would drift from the
  generator output on the next regeneration).
- ty version pinning is mandatory (`ty==0.0.74`): ty is 0.0.x preview with
  breaking changes between releases. Same for a compatible ruff pin.

## Config & packaging

pyproject.toml becomes the single source of truth; setup.cfg is deleted.

```toml
[project]
name = "python-materialsdb"
version = "0.1.0"          # moved from setup.cfg
requires-python = ">=3.10"
dependencies = ["lxml"]
# readme, license, authors, keywords, classifiers, urls mirror setup.cfg

[project.optional-dependencies]
ifc = ["ifcopenshell"]
dev = ["pytest-benchmark", "ruff==0.16.4", "ty==0.0.74"]

[build-system]  # unchanged backend: setuptools
[tool.setuptools.package-data]
"*" = ["*.json", "*.xsd"]
```

Ruff configuration:

```toml
[tool.ruff]
line-length = 120
target-version = "py310"
extend-exclude = ["src/materialsdb/classes.py"]
```

ty configuration: `[tool.ty]` table; classes.py ignored via per-path override;
any remaining diagnostics resolved by fix or targeted rule severity — no
blanket ignores.

## Code fixes

- `ruff check --fix` + `ruff format`: ~320 mechanical fixes (annotation
  modernization dominates).
- Manual fixes for the remainder (~40): unused imports/variables, import
  sorting fallout, invalid escape sequences, f-string cleanups.
- Justified inline suppressions only where behavior is intentional:
  - `BLE001` blind excepts in batch parsers (one bad file must not abort).
  - The deliberate naive-vs-aware datetime pair in `utils.date_from_xml /
    date_to_xml` (documented XML convention) — `DTZ` noqa with reason if tripped.
- 24 ty diagnostics resolved case-by-case; prefer real fixes over suppression.

## CI

`.github/workflows/ci.yml`, triggered on push and pull_request:

- ubuntu-latest, python '3.x' (style matches existing pypi_publish.yml)
- install `-e ".[ifc,dev]"`, then:
  `ruff check . && ruff format --check . && ty check . && pytest`
- No pytest-blender plugin exists on CI runners; plain pytest works there.

## Housekeeping riding along

- `.benchmarks/` added to `.gitignore`.
- AGENTS.md updated: ruff replaces black in Commands; version location note
  changes to pyproject.toml `[project] version`; CI existence noted;
  classes.py tooling-exclusion documented.

## Verification gates

1. Full test suite green after every change batch
   (`python3 -m pytest -p no:pytest-blender`; plain `pytest` on CI).
2. `ruff check .` exits 0; `ruff format --check .` exits 0.
3. `ty check .` exits 0.
4. `python -m build` produces sdist+wheel (packaging consolidation proof);
   wheel content still ships schema/*.xsd + material_psets.json.
5. Import smoke test from installed wheel (materialsdb.store queryable).

## Out of scope

- Migrating dev_utils/classes_generator.py output style (regeneration stays
  as-is; its cwd quirks are already documented).
- Adding type annotations beyond what zero-diagnostic requires.
- Any release/version bump (next release happens through the normal flow).
