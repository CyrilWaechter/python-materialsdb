# Tooling Migration Implementation Plan (ruff + ty + packaging + CI)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace black with ruff (format + lint, line-length 120), adopt ty, consolidate packaging into pyproject.toml, and add GitHub Actions CI enforcing all four gates.

**Architecture:** Config-first migration: pyproject.toml becomes the single packaging/tooling source of truth (setup.cfg deleted); ruff runs with its 0.16 default rule set to a zero-findings state (auto-fix batch, then exact manual edits, justified inline noqa only); ty configured with lxml-as-Any plus targeted per-file overrides for third-party/dynamic-typing noise; CI runs the four gates on every push/PR.

**Tech Stack:** ruff 0.16.4, ty 0.0.74, setuptools (existing backend), GitHub Actions, pytest (existing).

## Global Constraints

- line-length = 120; target-version = "py310"; requires-python = ">=3.10".
- Tool pins in the `dev` extra: `ruff==0.16.4`, `ty==0.0.74`, plus explicit `pytest`.
- `src/materialsdb/classes.py` is excluded from `ruff check`, `ruff format`, and `ty check` (generated file — must never drift from `dev_utils/classes_generator.py` output).
- Zero-error policy: `ruff check .`, `ruff format --check .`, and `ty check .` must ALL exit 0 at branch end; blanket category ignores are forbidden — only the specific entries this plan specifies are allowed.
- Inline `noqa` codes allowed ONLY where this plan lists them, each with a reason comment.
- Behavior must not change: the full test suite stays green (`python3 -m pytest -p no:pytest-blender` locally; plain `pytest` on CI) after every task. Benchmarks stay excluded by default (existing `-m 'not benchmark'` addopts preserved).
- setup.cfg is deleted in Task 1; version lives in pyproject.toml `[project] version = "0.1.0"` from then on.
- Commit style: short lowercase imperative.
- Local environment: always run pytest as `python3 -m pytest -p no:pytest-blender ...` (global pytest-blender plugin must be disabled). ruff 0.16.4 and ty 0.0.74 are installed system-wide (`/usr/bin/ruff`, `/usr/bin/ty`).
- Never touch: `tests/_stale_materialsdb.disabled` (symlink), untracked local files (.gitignore hunk `*.egg-info` is the maintainer's pending edit — LEAVE the worktree `.gitignore` modification alone except for the one append specified in Task 5).

## Baselines (verified 2026-08-24)

- `ruff check` (defaults, excluding classes.py path below): 362 findings, 320 auto-fixable `[*]`.
- Non-auto-fixable outside classes.py (the complete list Task 3 resolves):
  - `__init__.py`: F401 x5 (intentional re-exports)
  - `config.py`: UP035 Dict
  - `ifc/project_library.py`: F841 x5 (webinfo, labels, brushstyle, representation, pset), C408 x1
  - `query.py`: UP035 List
  - `serialiser.py`: BLE001 (from_xml_files worker), RUF013 (serialise `name` param)
  - `store.py`: UP035 List, BLE001 (refresh worker), DTZ005 (datetime.now without tz)
  - `summary.py`: UP035 Dict, Tuple
  - `utils.py`: DTZ001 (naive epoch constructor — intentional), SIM201, UP028
  - `dev_utils/classes_generator.py`: UP035 x2, SIM118
- `ty check`: 24 diagnostics (8 lxml-stub, 3 generator None-flow, 3 examples Optional-attr, 1 os.startfile, 2 ifcopenshell typing, 4 serialiser Optional-element, 3 store, 1 utils Webinfo, 1 tests/test_query real None-deref).
- `ruff format --check --line-length 120`: 16 of 24 tracked py files need reformatting.

---

### Task 1: Packaging consolidation into pyproject.toml

**Files:**
- Modify: `pyproject.toml`
- Delete: `setup.cfg`

**Interfaces:**
- Consumes: current setup.cfg metadata (name python-materialsdb, version 0.0.2->now 0.1.0, author Cyril Waechter cyrwae@hotmail.com, description, license GPLv3+, keywords materials/BIM/ifcopenshell/IFC, url https://github.com/CyrilWaechter/python-materialsdb, classifiers list)
- Produces: pyproject-only packaging that `python -m build` and `pip install -e ".[ifc,dev]"` support identically; `[project]` table later tasks extend with tool tables.

- [ ] **Step 1: Rewrite pyproject.toml**

Replace the entire contents of `pyproject.toml` with:

```toml
[build-system]
requires = ["setuptools>=64", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "python-materialsdb"
version = "0.1.0"
description = "A library to work with materialsdb.org open standard for building materials."
readme = "README.md"
license = "GPL-3.0-or-later"
authors = [{ name = "Cyril Waechter", email = "cyrwae@hotmail.com" }]
keywords = ["materials", "BIM", "ifcopenshell", "IFC"]
requires-python = ">=3.10"
classifiers = [
    "Programming Language :: Python :: 3",
    "Operating System :: OS Independent",
    "Topic :: Software Development :: Libraries :: Python Modules",
    "Topic :: Scientific/Engineering",
]

[project.urls]
Homepage = "https://github.com/CyrilWaechter/python-materialsdb"
Bug Tracker = "https://github.com/CyrilWaechter/python-materialsdb/issues"
Community = "https://community.osarch.org/"

[project.optional-dependencies]
ifc = ["ifcopenshell"]
dev = [
    "pytest",
    "pytest-benchmark",
    "ruff==0.16.4",
    "ty==0.0.74",
]

[tool.setuptools]
include-package-data = true

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
"*" = ["*.json", "*.xsd"]

[tool.pytest.ini_options]
testpaths = [
    "tests",
]
markers = [
    "benchmark: performance benchmarks (deselected by default)",
]
addopts = "-m 'not benchmark'"
```

Notes: `license = "GPL-3.0-or-later"` uses SPDX string form (PEP 639); drop the old `License :: OSI Approved` classifier (redundant with SPDX and rejected by modern PyPI validation when both present).

- [ ] **Step 2: Delete setup.cfg**

```bash
git rm setup.cfg
```

- [ ] **Step 3: Verify install + tests**

Run:

```bash
pip install -e ".[ifc,dev]" && python3 -m pytest -p no:pytest-blender -q
```

Expected: editable install succeeds; suite green (22 passed, 6 deselected).

- [ ] **Step 4: Verify build + wheel contents**

```bash
python -m build --outdir /tmp/opencode/dist-check && python -m zipfile -l /tmp/opencode/dist-check/*.whl | grep -E "materialsdb103.xsd|material_psets.json" && ls /tmp/opencode/dist-check/*.tar.gz
```

Expected: sdist + wheel built; wheel contains `materialsdb/schema/materialsdb103.xsd` and `materialsdb/ifc/material_psets.json`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml setup.cfg
git commit -m "build!: consolidate packaging metadata into pyproject.toml"
```

---

### Task 2: Ruff configuration + format + safe auto-fixes

**Files:**
- Modify: `pyproject.toml`
- Modify: many tracked .py files (format + autofix fallout)

**Interfaces:**
- Consumes: Task 1's pyproject structure
- Produces: `[tool.ruff]` config other tasks rely on; repo formatted at 120 cols; remaining findings exactly the non-auto-fixable baseline minus classes.py items.

- [ ] **Step 1: Add ruff config**

Append to `pyproject.toml`:

```toml
[tool.ruff]
line-length = 120
target-version = "py310"
extend-exclude = [
    "src/materialsdb/classes.py",
]
```

Remove the `[tool.black]` section if still present (it was dropped in Task 1's rewrite — verify).

- [ ] **Step 2: Auto-fix then format**

```bash
ruff check --exclude src/materialsdb/classes.py --fix src tests dev_utils examples
ruff format --exclude src/materialsdb/classes.py .
```

(`--fix` applies only `[*]`-marked safe fixes: UP045/UP006/I001/RUF010/F541/W605/UP033 and the fixable UP035/F401 subset.)

- [ ] **Step 3: Verify tests + measure remainder**

```bash
python3 -m pytest -p no:pytest-blender -q
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples --output-format concise | tail -3
```

Expected: suite green; remainder is ONLY the Global Constraints' non-auto-fixable list (~30 findings; count will differ slightly from 42 because some UP035/F401 were auto-fixed).

- [ ] **Step 4: Commit**

```bash
git add -A src tests dev_utils examples pyproject.toml
git commit -m "style: adopt ruff formatting and apply safe lint autofixes"
```

---

### Task 3: Manual lint fixes to zero

**Files:**
- Modify: `src/materialsdb/__init__.py`, `src/materialsdb/config.py`, `src/materialsdb/query.py`, `src/materialsdb/serialiser.py`, `src/materialsdb/store.py`, `src/materialsdb/summary.py`, `src/materialsdb/utils.py`, `src/materialsdb/ifc/project_library.py`, `dev_utils/classes_generator.py`

**Interfaces:**
- Produces: `ruff check` exits 0. Signature change: `XmlSerialiser.serialise(self, element, name: str | None = None)` (RUF013). `store.refresh` timestamp becomes timezone-aware UTC. NO behavior changes.

Apply each fix exactly:

- [ ] **Step 1: `src/materialsdb/__init__.py` — declare re-exports**

Replace contents with:

```python
from materialsdb import cache, classes, config, ifc, serialiser

__all__ = ["cache", "classes", "config", "ifc", "serialiser"]
```

- [ ] **Step 2: typing deprecation imports (UP035)**

In each file, replace deprecated imports with builtin equivalents and adjust annotations accordingly:
- `config.py`: `from typing import Dict` -> delete; `def get_base_config() -> dict[str, str]:`
- `query.py`: remove `List` from the typing import; change `List[MaterialSummary]` -> `list[MaterialSummary]` (both occurrences)
- `store.py`: remove `List` from imports; `-> List[MaterialSummary]` -> `-> list[MaterialSummary]`; `_sorted(results: List[...]...)` -> `list[...]` (both spots)
- `summary.py`: remove `Dict, Tuple` from `typing` import; `Dict[str, str]` -> `dict[str, str]` everywhere (annotations AND `result: Dict[str, str] = {}` locals); `Tuple[Optional[float], Optional[float]]` -> `tuple[float | None, float | None]`; while there, replace remaining `Optional[X]` with `X | None` in this file's signatures for consistency
- `dev_utils/classes_generator.py`: `from typing import List, Dict, Any` -> `from typing import Any`; `List[...]` -> `list[...]`, `Dict[str, PyAttr]` style annotations updated correspondingly (PyAttr fields and XsdExtractor attributes)

- [ ] **Step 3: `ifc/project_library.py` — unused locals + C408**

Delete the assignments (keep the calls where they have side effects):
- Line ~145: `webinfo = utils.get_material_webinfo(material, self.lang)` -> bare call `utils.get_material_webinfo(material, self.lang)`
- Line ~147: delete `labels = material.information.labels`
- Line ~149: delete `brushstyle = material.information.BrushStyle`
- Line ~153: `representation = file.createIfcStyledRepresentation(...)` -> bare call
- Line ~161: `properties = list()` -> `properties = []`
- Line ~180: `pset = file.create_entity(...)` -> bare call

- [ ] **Step 4: `utils.py` — SIM201, UP028, DTZ001 noqa**

- SIM201: `if not material_country == country:` -> `if material_country != country:`
- UP028: in `get_material_layers`, replace the for/yield with:
  ```python
  yield from getattr(getattr(material, "layers", ()), "layer", ())
  ```
- DTZ001: `date_from_xml` intentionally constructs a NAIVE datetime (XML convention pairs naive here with aware `date_to_xml`; changing it alters comparisons downstream). Annotate with noqa:
  ```python
  return datetime.datetime(1899, 12, 30) + datetime.timedelta(days=days)  # noqa: DTZ001 - naive-by-design, see date_to_xml
  ```

- [ ] **Step 5: `serialiser.py` — BLE001 noqa, RUF013 fix**

- BLE001 in `from_xml_files.load`: intentional (one bad file must not abort the batch). Change to:
  ```python
        except Exception as err:  # noqa: BLE001 - batch parsing must survive bad files
  ```
- RUF013: `def serialise(self, element, name: str | None = None):` (was implicit Optional `name: str = None`)

- [ ] **Step 6: `store.py` — BLE001 noqa, DTZ005 fix**

- BLE001 in `refresh`: `except Exception as err:  # noqa: BLE001 - one bad producer must not abort refresh`
- DTZ005: `(str(path), digest, datetime.datetime.now().timestamp())` ->
  ```python
  (str(path), digest, datetime.datetime.now(datetime.timezone.utc).timestamp())
  ```
  (timestamp() of aware-UTC equals old wall-clock epoch value; no behavior change)

- [ ] **Step 7: Verify zero + green**

```bash
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples && echo LINT-CLEAN
python3 -m pytest -p no:pytest-blender -q
```

Expected: `LINT-CLEAN` printed; suite green. If any unexpected residual finding appears, fix it in kind (real fix preferred, noqa-with-reason only if intentional-behavior) rather than widening config.

- [ ] **Step 8: Commit**

```bash
git add src dev_utils
git commit -m "style: resolve remaining lint findings, ruff check clean"
```

---

### Task 4: Adopt ty to zero diagnostics

**Files:**
- Modify: `pyproject.toml`, `tests/test_query.py`, possibly `src/materialsdb/serialiser.py`, `src/materialsdb/store.py`, `src/materialsdb/utils.py`

**Interfaces:**
- Consumes: Task 1 pins (`ty==0.0.74`)
- Produces: `ty check` exits 0 with config committed; future code is type-checked in CI.

- [ ] **Step 1: Add ty config**

Append to `pyproject.toml`:

```toml
[tool.ty.src]
include = ["src", "tests", "dev_utils", "examples"]

[[tool.ty.overrides]]
# Generated file: excluded from ruff/ty per Global Constraints (no-drift policy).
include = ["src/materialsdb/classes.py"]
[tool.ty.overrides.rules]
all = "ignore"

[tool.ty.analysis]
# lxml ships no type information; treat as Any instead of erroring on every use.
replace-imports-with-any = ["lxml.**"]

[[tool.ty.overrides]]
# Generator emits dynamic setattr-based classes; ty cannot follow the flow of
# self.file (assigned inside parse_schema) nor os.startfile (Windows-only).
include = ["dev_utils/classes_generator.py", "examples/generate_ifc_project_libraries.py"]
[tool.ty.overrides.rules]
unresolved-attribute = "ignore"

[[tool.ty.overrides]]
# Examples exercise the generated model whose Optional list fields are
# populated before use; runtime-valid, inference-hostile.
include = ["examples/create_layers.py"]
[tool.ty.overrides.rules]
unresolved-attribute = "ignore"
possibly-missing-attribute = "ignore"

[[tool.ty.overrides]]
# ifcopenshell's own annotations disagree with its runtime API here.
include = ["src/materialsdb/ifc/project_library.py"]
[tool.ty.overrides.rules]
invalid-argument-type = "ignore"
invalid-assignment = "ignore"
```

- [ ] **Step 2: Fix the real findings**

- `tests/test_query.py::test_get_material`: guard the deref:
  ```python
  def test_get_material(isolated_store):
      material = query.get_material("00000000-0000-0000-0000-000000000002")
      assert material is not None
      assert material.information.group == "Concrete"
  ```
- Run `ty check --project .` and resolve any SURVIVING diagnostics one by one, preferring real fixes; expected survivors (verify each against source before suppressing): `store.py` `.material` on from_element result and `utils.py` Webinfo construction — if they stem from ty inferring generated-class `__new__` signatures oddly, add ONE more override block:
  ```toml
  [[tool.ty.overrides]]
  include = ["src/materialsdb/store.py", "src/materialsdb/utils.py", "src/materialsdb/serialiser.py"]
  [tool.ty.overrides.rules]
  unresolved-attribute = "ignore"
  invalid-argument-type = "ignore"
  ```
  Only include rules/files for diagnostics you actually reproduced; do not pre-ignore.

- [ ] **Step 3: Verify all three gates + green suite**

```bash
ty check --project . && echo TY-CLEAN
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples && echo LINT-CLEAN
python3 -m pytest -p no:pytest-blender -q
```

Expected: TY-CLEAN, LINT-CLEAN, suite green (23 passed now — one added assertion line does not change counts; expect 22 passed unless new tests added).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src tests
git commit -m "chore: adopt ty type checking, diagnostics clean"
```

---

### Task 5: CI workflow, housekeeping, docs

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `.gitignore` (append one line), `AGENTS.md`

**Interfaces:**
- Consumes: everything above
- Produces: enforced CI gates + accurate agent/maintainer documentation.

- [ ] **Step 1: Create CI workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [master]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.x'
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[ifc,dev]"
      - name: Lint (ruff)
        run: |
          ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples
          ruff format --exclude src/materialsdb/classes.py --check .
      - name: Type check (ty)
        run: ty check --project .
      - name: Tests
        run: pytest
```

Note: CI runners have no pytest-blender plugin, so plain `pytest` is correct there. Do NOT add `-p no:pytest-blender` to CI.

- [ ] **Step 2: Housekeeping — .gitignore**

Append a single line to `.gitignore` (do not touch the maintainer's existing uncommitted `materialsdb.egg-info` -> `*.egg-info` modification beyond adding your line to the current working copy):

```text
.benchmarks/
.superpowers/
```

- [ ] **Step 3: Update AGENTS.md**

Rewrite these sections of `AGENTS.md`:
- Commands: replace the black line with
  ```bash
  ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples   # lint
  ruff format --exclude src/materialsdb/classes.py .                              # format
  ty check --project .                                                            # type check
  ```
  and note: formatter/linter is ruff (NOT black), line-length 120; local pytest needs `-p no:pytest-blender` (global plugin hijack) while CI uses plain `pytest`.
- Generated code section: add that classes.py is also excluded from ruff and ty for the same no-drift reason.
- Release/versioning: version now lives in `pyproject.toml` `[project] version` (NOT setup.cfg — deleted; NOT a separate field).
- Add a CI line: `.github/workflows/ci.yml` runs ruff check/format, ty, pytest on push/PR.
- Test quirks: replace the stale `tests/materialsdb` duplicate warning with: `tests/_stale_materialsdb.disabled` is a symlink to `../src/materialsdb/` kept out of import paths — never restore the original name.
- Packaging notes: package_data now configured at `[tool.setuptools.package-data]` in pyproject.toml.

- [ ] **Step 4: Full verification**

```bash
python3 -m pytest -p no:pytest-blender -q
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples && echo LINT-CLEAN
ruff format --exclude src/materialsdb/classes.py --check . && echo FORMAT-CLEAN
ty check --project . && echo TY-CLEAN
rm -rf /tmp/opencode/dist-final && python -m build --outdir /tmp/opencode/dist-final && python -m zipfile -l /tmp/opencode/dist-final/*.whl | grep -E "xsd|json"
```

Expected: suite green; all three CLEAN echoes; wheel still bundles schema/*.xsd + material_psets.json.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml .gitignore AGENTS.md
git commit -m "ci: enforce ruff, ty and pytest on push and pull requests"
```
