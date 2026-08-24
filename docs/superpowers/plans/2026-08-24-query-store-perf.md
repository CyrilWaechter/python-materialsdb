# Indexed Query Layer + Performance Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Near-instant single-material fetch by id and fast sorted/filtered listing of all materials, backed by a SQLite store built from the XML cache, plus benchmark-driven parser speedups.

**Architecture:** New `summary.py` (lightweight material record + extraction rules) and `store.py` (stdlib sqlite3, WAL, per-producer incremental refresh) layered on top of the existing generated dataclasses and `cache.py`. `serialiser.py` gets a per-class type-hint cache and parallel multi-file parsing. `query.py` becomes a thin facade over `MaterialStore`. Benchmarks gate every optimization.

**Tech Stack:** Python 3 stdlib (sqlite3, hashlib, concurrent.futures, functools), lxml/objectify (existing), pytest + pytest-benchmark (dev extra).

## Global Constraints

- Core runtime dependency stays lxml only; sqlite3/hashlib/concurrent.futures are stdlib (spec dependency policy: perf-justified adds allowed, none needed).
- API breaks are approved (library pre-1.0): removing `query.SOURCES`, `query.get_by_name`, `query.get_by_producer` is in scope.
- No network access in tests; no dependency on untracked `example_v103.xml`.
- Formatting: black (current repo standard); ruff/ty migration is a separate sub-project — do not do it here.
- Schema target is materialsdb103 (`serialiser.get_xml_schema()`); classes come from `src/materialsdb/classes.py` — never hand-edit beyond what tasks specify.
- Version bump / release does NOT happen in this plan.
- All commands run from repo root `/home/cyril/git/materialsdb`; plain `pytest` discovers everything under `tests/` (configured via `testpaths`) minus benchmarks (excluded in Task 2).
- Commit style: short lowercase imperative matching existing history (e.g. `Add github action to publish to pypi`).

## Key existing signatures (for orientation)

```python
# serialiser.py
XmlDeserialiser().from_xml(xml_path: str, assert_schema: bool = False) -> classes.Materials
XmlDeserialiser().from_element(element, base_class=None)          # recursive element -> dataclass
serialiser.get_valid_root(tree) -> root                            # injects missing sig/publickey
# cache.py
cache.get_cache_folder() -> pathlib.Path                           # ~/.cache/materialsdb (XDG/APPDATA aware)
cache.producers() -> Iterator[pathlib.Path]                        # xml files in <cache>/Producers
cache.Report = namedtuple("Report", ["existing", "updated", "deleted"])
# utils.py
utils.get_by_country(values, country)                              # exact match, else country=None entry, else None
utils.get_material_layers(material) -> Generator[Layer]
```

---

### Task 1: Committed test fixtures + conftest

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/fixtures/mini_producer.xml`
- Modify: `tests/test_serialiser.py` (append one test)

**Interfaces:**
- Consumes: `XmlDeserialiser.from_xml`
- Produces: `tests/fixtures/mini_producer.xml` — deterministic 3-material v103 XML used by all later tasks. Facts later tasks rely on: ids end `001`/`002`/`003`; company `Mini SA`; material 1 = Insulation, wall, names fr/de/(unspecified), explanations fr/de, layer1 CH+FR localized thermal+geometry, layer2 unlocalized; material 2 = Concrete, single layer (CH-only thermal λ=0.21, unlocalized geometry thick=150); material 3 = Others, no layers. Also conftest fixtures `mini_xml: Path` and `mini_source`.

- [ ] **Step 1: Write conftest**

Create `tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))

import pytest

from materialsdb.serialiser import XmlDeserialiser

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mini_xml() -> Path:
    return FIXTURES / "mini_producer.xml"


@pytest.fixture
def mini_source(mini_xml):
    return XmlDeserialiser().from_xml(str(mini_xml))
```

- [ ] **Step 2: Write the fixture XML**

Create `tests/fixtures/mini_producer.xml`. No sig/publickey elements — `serialiser.get_valid_root` injects them during deserialisation. ASCII characters only:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<materials company="Mini SA" companyid="A1B85A67-5B1E-4960-A297-2DE8275049C5" ver="1" crd="43979.7189866898" verXML="3" xmlns="http://www.materialsdb.org">
  <material id="00000000-0000-0000-0000-000000000001" readonly="1" type="simple">
    <information group="Insulation" wall="1" color="16711680">
      <names>
        <name lang="fr">Isolant A</name>
        <name lang="de">Daemmstoff A</name>
        <name>Material A</name>
      </names>
      <explanations>
        <explanation lang="fr">Panneau isolant</explanation>
        <explanation lang="de">Daemmplatte</explanation>
      </explanations>
    </information>
    <layers>
      <layer id="00000000-0000-0000-0000-0000000000a1">
        <geometry country="CH" thick="200"/>
        <thermal country="CH" lambda_value="0.036"/>
        <geometry country="FR" thick="240"/>
        <thermal country="FR" lambda_value="0.04"/>
      </layer>
      <layer id="00000000-0000-0000-0000-0000000000a2">
        <geometry thick="100"/>
        <thermal lambda_value="0.05"/>
      </layer>
    </layers>
  </material>
  <material id="00000000-0000-0000-0000-000000000002" readonly="1" type="simple">
    <information group="Concrete">
      <names>
        <name lang="fr">Beton B</name>
      </names>
    </information>
    <layers>
      <layer id="00000000-0000-0000-0000-0000000000b1">
        <geometry thick="150"/>
        <thermal country="CH" lambda_value="0.21"/>
      </layer>
    </layers>
  </material>
  <material id="00000000-0000-0000-0000-000000000003" readonly="1" type="simple">
    <information group="Others">
      <names>
        <name lang="fr">Sans donnees C</name>
      </names>
    </information>
  </material>
</materials>
```

- [ ] **Step 3: Append sanity roundtrip test**

Append to `tests/test_serialiser.py` (deduplicate the `Path` import if already present):

```python
def test_mini_fixture_roundtrip(tmp_path, mini_source):
    assert len(mini_source.material) == 3
    assert mini_source.company == "Mini SA"
    out = tmp_path / "roundtrip.xml"
    XmlSerialiser().to_xml(mini_source, xml_path=str(out))
    reparsed = XmlDeserialiser().from_xml(str(out))
    assert len(reparsed.material) == 3
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_serialiser.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/fixtures/mini_producer.xml tests/test_serialiser.py
git commit -m "test: add committed mini producer fixture and shared conftest"
```

---

### Task 2: Benchmark harness + parsing baselines

**Files:**
- Modify: `pyproject.toml` (`[tool.pytest.ini_options]`)
- Modify: `setup.cfg` (`[options.extras_require]`)
- Create: `tests/benchmarks/test_parse_benchmarks.py`

**Interfaces:**
- Consumes: `XmlDeserialiser.from_xml`, `cache.producers`
- Produces: command `pytest -m benchmark` runs ONLY benchmarks; plain `pytest` excludes them. Saved baseline group named `baseline` via `--benchmark-save=baseline`; later tasks compare by running `pytest -m benchmark --benchmark-compare=0001_baseline` (use whatever numeric prefix `--benchmark-save` reported, visible under `.benchmarks/<machine>/`).

- [ ] **Step 1: Register marker, default exclusion, dev extra**

`pyproject.toml` — extend the existing pytest section:

```toml
[tool.pytest.ini_options]
testpaths = [
    "tests",
]
markers = [
    "benchmark: performance benchmarks (deselected by default)",
]
addopts = "-m 'not benchmark'"
```

`setup.cfg` — extend `[options.extras_require]`:

```ini
[options.extras_require]
ifc = ifcopenshell
dev = pytest-benchmark
```

Install: `pip install -e ".[ifc,dev]"` (or minimally `pip install pytest-benchmark`).

- [ ] **Step 2: Write benchmark file**

Create `tests/benchmarks/test_parse_benchmarks.py`. Note: reference the fixture by direct path, NOT via the function-scoped `mini_xml` conftest fixture, because benchmark fixtures below are module-scoped:

```python
from pathlib import Path

import pytest

from materialsdb import cache
from materialsdb.serialiser import XmlDeserialiser

MINI_XML = Path(__file__).parents[1] / "fixtures" / "mini_producer.xml"
DESERIALISER = XmlDeserialiser()


def test_parse_mini_fixture(benchmark):
    benchmark(DESERIALISER.from_xml, str(MINI_XML))


@pytest.mark.skipif(
    not next(cache.producers(), None),
    reason="no local producers cache",
)
def test_parse_all_cached_producers(benchmark):
    def parse_all():
        for producer in cache.producers():
            DESERIALISER.from_xml(str(producer))

    benchmark(parse_all)


@pytest.mark.skipif(
    next(cache.producers(), None) is None,
    reason="no local producers cache",
)
def test_parse_largest_cached_producer(benchmark):
    largest = max(cache.producers(), key=lambda p: p.stat().st_size)
    benchmark(DESERIALISER.from_xml, str(largest))
```

- [ ] **Step 3: Verify exclusion from default run**

Run: `pytest`
Expected: existing tests run; benchmark items reported as deselected; no failures.

- [ ] **Step 4: Record baselines**

Run: `pytest -m benchmark --benchmark-save=baseline`
Expected: cache-dependent benches skip cleanly when `~/.cache/materialsdb/Producers` is absent. Record mean times here before optimizing:

- parse_mini_fixture: `<mean>`
- parse_largest_cached_producer: `<mean>` (or SKIPPED)
- parse_all_cached_producers: `<mean>` (or SKIPPED)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml setup.cfg tests/benchmarks/
git commit -m "test: add pytest-benchmark harness and parsing baselines"
```

---

### Task 3: Cache type hints per class in XmlDeserialiser

**Files:**
- Modify: `src/materialsdb/serialiser.py`
- Modify: `tests/test_serialiser.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `serialiser.cached_type_hints(cls) -> dict` — `functools.lru_cache`-wrapped `typing.get_type_hints`. Public behavior of `from_element`/`from_xml` unchanged.

- [ ] **Step 1: Write failing test**

Append to `tests/test_serialiser.py`:

```python
import pytest


@pytest.mark.skipif(
    not Path("example_v103.xml").exists(),
    reason="example_v103.xml not present locally",
)
def test_type_hints_are_cached():
    from materialsdb import serialiser

    serialiser.cached_type_hints.cache_clear()
    deserialiser = XmlDeserialiser()
    deserialiser.from_xml("example_v103.xml")
    hits_after_first = serialiser.cached_type_hints.cache_info().hits
    deserialiser.from_xml("example_v103.xml")
    assert serialiser.cached_type_hints.cache_info().hits > hits_after_first
```

Run: `pytest tests/test_serialiser.py::test_type_hints_are_cached -v`
Expected: FAIL with `AttributeError: module 'materialsdb.serialiser' has no attribute 'cached_type_hints'` (or PASS-as-skip if the big example file is absent locally — in that case additionally verify manually below).

Manual verification fallback (always valid): `python -c "from pathlib import Path; import sys; sys.path.append('src'); from materialsdb import serialiser; from materialsdb.serialiser import XmlDeserialiser; serialiser.cached_type_hints.cache_clear(); XmlDeserialiser().from_xml('tests/fixtures/mini_producer.xml')"` must not raise after implementation.

- [ ] **Step 2: Implement**

In `src/materialsdb/serialiser.py` add import and module-level function:

```python
from functools import lru_cache


@lru_cache(maxsize=None)
def cached_type_hints(cls) -> dict:
    return typing.get_type_hints(cls)
```

In `XmlDeserialiser.from_element` replace:

```python
type_hints = typing.get_type_hints(element_class)
```

with:

```python
type_hints = cached_type_hints(element_class)
```

The returned dict is treated read-only by existing code (lookups only), so returning the cached dict itself is safe.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_serialiser.py -v`
Expected: PASS including new cache test (or skip-with-manual-check as above).

- [ ] **Step 4: Measure impact**

Run: `pytest -m benchmark --benchmark-compare=<N>_baseline` (replace `<N>` with the saved run number from `.benchmarks/`)
Expected: clear drop in parse times (expect several-fold on large files — `get_type_hints` was called once per XML element). If regression or zero gain, STOP and profile before committing.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/serialiser.py tests/test_serialiser.py
git commit -m "perf: cache typing.get_type_hints per class in XmlDeserialiser"
```

---

### Task 4: Parallel multi-producer parsing

**Files:**
- Modify: `src/materialsdb/serialiser.py`
- Modify: `tests/test_serialiser.py`

**Interfaces:**
- Consumes: `XmlDeserialiser.from_xml`
- Produces: `XmlDeserialiser.from_xml_files(paths, max_workers=None) -> Iterator[tuple[Path, Materials | None]]` — yields `(path, source)` per input; unparsable files yield `(path, None)` after printing an error. Used by Task 6 `refresh`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_serialiser.py`:

```python
import shutil


def test_from_xml_files_reports_corrupt_and_parses_rest(tmp_path, mini_xml):
    good_a = tmp_path / "a.xml"
    good_b = tmp_path / "b.xml"
    bad = tmp_path / "bad.xml"
    shutil.copy(mini_xml, good_a)
    shutil.copy(mini_xml, good_b)
    bad.write_text("<materials><unclosed>", encoding="utf-8")

    results = {
        path.name: source
        for path, source in XmlDeserialiser().from_xml_files([good_a, good_b, bad])
    }

    assert results["a.xml"] is not None
    assert len(results["a.xml"].material) == 3
    assert results["b.xml"] is not None
    assert results["bad.xml"] is None
```

Run: `pytest tests/test_serialiser.py::test_from_xml_files_reports_corrupt_and_parses_rest -v`
Expected: FAIL with `AttributeError: 'XmlDeserialiser' object has no attribute 'from_xml_files'`

- [ ] **Step 2: Implement**

Add import in `src/materialsdb/serialiser.py`:

```python
from concurrent.futures import ThreadPoolExecutor
```

Add method to `XmlDeserialiser`:

```python
def from_xml_files(self, paths, max_workers=None):
    """Parse multiple producer XML files concurrently.

    Yields (path, Materials) per successfully parsed file and
    (path, None) for files that could not be parsed."""

    def load(path):
        try:
            return Path(path), self.from_xml(str(path))
        except Exception as err:
            print(f"{Path(path).name}: could not parse file:\n\t{err}")
            return Path(path), None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        yield from executor.map(load, paths)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_serialiser.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/materialsdb/serialiser.py tests/test_serialiser.py
git commit -m "feat: parallel multi-file parsing with per-file error reporting"
```

---

### Task 5: MaterialSummary extraction (`summary.py`)

**Files:**
- Create: `src/materialsdb/summary.py`
- Create: `tests/test_summary.py`

**Interfaces:**
- Consumes: `classes.Material`, `utils.get_by_country(values, country)`, `utils.get_material_layers(material)`, `config.get_country()`
- Produces (used by Tasks 6–8):

```python
@dataclass
class MaterialSummary:
    id: str
    company_id: str
    company: str
    category: str
    names: Dict[str, str]         # lang code ("" when unspecified) -> name
    descriptions: Dict[str, str]  # same keying
    lambda_min: Optional[float]
    lambda_max: Optional[float]
    thick_min: Optional[float]
    thick_max: Optional[float]
    usage: Dict[str, bool]        # keys: wall, roof, floor, door


def summarize_material(
    material: Material,
    company_id: str = "",
    company: str = "",
    country: Optional[str] = None,   # None -> config.get_country()
) -> MaterialSummary
```

Aggregation rule: per layer, resolve `utils.get_by_country(layer.thermal or (), country).lambda_value` and `utils.get_by_country(layer.geometry or (), country).thick`; ignore Nones; min/max across layers.

- [ ] **Step 1: Write failing tests**

Create `tests/test_summary.py`:

```python
from materialsdb.summary import summarize_material


def test_insulation_material_summary_ch(mini_source):
    material = mini_source.material[0]
    s = summarize_material(material, country="CH")

    assert s.id == "00000000-0000-0000-0000-000000000001"
    assert s.category == "Insulation"
    assert s.names == {"fr": "Isolant A", "de": "Daemmstoff A", "": "Material A"}
    assert s.descriptions == {"fr": "Panneau isolant", "de": "Daemmplatte"}
    assert s.usage["wall"] is True
    assert s.usage["roof"] is False
    assert s.thick_min == 100
    assert s.thick_max == 200
    assert s.lambda_min == 0.036
    assert s.lambda_max == 0.05


def test_french_country_picks_fr_values(mini_source):
    s = summarize_material(mini_source.material[0], country="FR")
    assert s.lambda_min == 0.04
    assert s.lambda_max == 0.05
    assert s.thick_min == 100
    assert s.thick_max == 240


def test_concrete_single_layer(mini_source):
    s = summarize_material(mini_source.material[1], country="CH")
    assert s.category == "Concrete"
    assert s.names == {"fr": "Beton B"}
    assert s.descriptions == {}
    assert s.lambda_min == s.lambda_max == 0.21
    assert s.thick_min == s.thick_max == 150


def test_material_without_layers_has_none_metrics(mini_source):
    s = summarize_material(mini_source.material[2])
    assert s.lambda_min is None
    assert s.thick_max is None
    assert s.usage == {"wall": False, "roof": False, "floor": False, "door": False}
```

Expected-value rationale (from `utils.get_by_country` semantics — exact country match first, then entries with no country attribute): material 1 with country FR picks layer1-FR thermal (λ 0.04) plus layer2 unlocalized thermal via fallback (λ 0.05); geometry likewise FR thick=240 and fallback thick=100.

Run: `pytest tests/test_summary.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'materialsdb.summary'`)

- [ ] **Step 2: Implement**

Create `src/materialsdb/summary.py`:

```python
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
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_summary.py -v`
Expected: PASS (all 4)

- [ ] **Step 4: Commit**

```bash
git add src/materialsdb/summary.py tests/test_summary.py
git commit -m "feat: add MaterialSummary extraction from material dataclasses"
```

---

### Task 6: SQLite store (`store.py`)

**Files:**
- Create: `src/materialsdb/store.py`
- Create: `tests/test_store.py`

**Interfaces:**
- Consumes: `XmlDeserialiser.from_element`, `serialiser.get_valid_root`, `summarize_material`, `cache.get_cache_folder`, `cache.producers`, `cache.Report`
- Produces (used by Tasks 7–8):

```python
SCHEMA_VERSION = "1"

class MaterialStore:
    def __init__(self, db_path: Optional[Path] = None) -> None
        # default db: cache.get_cache_folder() / "materials.db"

    def refresh(self, force: bool = False,
                paths: Optional[Iterable[Path]] = None) -> Report
        # paths=None -> cache.producers(). Hashes each file; re-upserts changed/new;
        # deletes rows of files no longer in paths; force=True re-upserts all given.
        # Returns Report(existing=[unchanged Paths], updated=[rebuilt Paths],
        #                deleted=[removed Paths]). Corrupt files: printed, skipped.

    def summaries(self, company=None, category=None, min_lambda=None,
                  max_lambda=None, min_thick=None, max_thick=None,
                  usage=None, text=None, sort="company", ascending=True,
                  lang=None) -> list[MaterialSummary]
        # numeric ranges intersect material ranges:
        #   min_lambda -> lambda_max >= min_lambda ; max_lambda -> lambda_min <= max_lambda
        #   min_thick  -> thick_max >= min_thick   ; max_thick  -> thick_min <= max_thick
        # usage: "wall"|"roof"|"floor"|"door"; text: case-insensitive substring of
        # configured-lang name (falls back to "" lang); NULL metrics sort last.

    def get(self, material_id: str) -> Optional[Material]
        # lazy: xml BLOB -> objectify.fromstring -> from_element

    def close(self) -> None
```

DB schema (adds `source_file` column beyond the spec's column list — required so rows can be deleted when a producer file disappears):

```sql
CREATE TABLE IF NOT EXISTS materials (
    id TEXT PRIMARY KEY, company_id TEXT, company TEXT, category TEXT,
    names TEXT, descriptions TEXT,
    lambda_min REAL, lambda_max REAL, thick_min REAL, thick_max REAL,
    usage TEXT, source_file TEXT, xml BLOB);
CREATE INDEX IF NOT EXISTS idx_company ON materials(company);
CREATE INDEX IF NOT EXISTS idx_category ON materials(category);
CREATE INDEX IF NOT EXISTS idx_lambda ON materials(lambda_min);
CREATE TABLE IF NOT EXISTS producer_files (
    path TEXT PRIMARY KEY, sha256 TEXT, built_at REAL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
```

- [ ] **Step 1: Write failing tests**

Create `tests/test_store.py`:

```python
import pytest

from materialsdb.store import MaterialStore


@pytest.fixture
def store(tmp_path, mini_xml):
    s = MaterialStore(db_path=tmp_path / "test.db")
    s.refresh(paths=[mini_xml])
    yield s
    s.close()


def test_refresh_populates_all_materials(store):
    assert len(store.summaries()) == 3


def test_get_returns_full_material_equivalent_to_direct_parse(store, mini_source):
    material = store.get("00000000-0000-0000-0000-000000000001")
    original = mini_source.material[0]
    assert str(material.information.names.name[0]) == str(original.information.names.name[0])
    assert material.information.group == "Insulation"
    assert material.layers.layer[0].thermal[0].lambda_value == 0.036


def test_get_unknown_id_returns_none(store):
    assert store.get("nope") is None


def test_filters(store):
    insulation = store.summaries(category="Insulation")
    assert len(insulation) == 1
    assert insulation[0].id.endswith("001")

    assert len(store.summaries(company="Mini SA")) == 3

    low_lambda = store.summaries(max_lambda=0.1)
    assert {s.id[-3:] for s in low_lambda} == {"001"}

    walls = store.summaries(usage="wall")
    assert len(walls) == 1


def test_sort_order_and_nulls_last(store):
    rows = store.summaries(sort="lambda")
    assert [r.lambda_min for r in rows] == [0.036, 0.21, None]


def test_text_filter_uses_configured_lang(monkeypatch, store):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    rows = store.summaries(text="isol")
    assert len(rows) == 1
    assert rows[0].id.endswith("001")


def test_refresh_is_incremental(store, mini_xml):
    report = store.refresh(paths=[mini_xml])
    assert report.updated == []
    assert len(report.existing) == 1


def test_refresh_rebuilds_changed_file(store, mini_xml, tmp_path):
    copy = tmp_path / "changed.xml"
    copy.write_text(
        mini_xml.read_text(encoding="utf-8").replace("0.036", "0.03"),
        encoding="utf-8",
    )

    report = store.refresh(paths=[copy])
    assert copy in report.updated
    rows = store.summaries(min_lambda=0.03)
    assert len(rows) == 1
    assert rows[0].lambda_min == 0.03


def test_deleted_producer_rows_are_removed(store):
    store.refresh(paths=[])  # declare current file set empty
    assert store.summaries() == []


def test_corrupt_producer_is_skipped(store, tmp_path, mini_xml):
    bad = tmp_path / "bad.xml"
    bad.write_text("<materials>", encoding="utf-8")
    report = store.refresh(force=True, paths=[mini_xml, bad])
    assert len(report.existing) + len(report.updated) == 1
    assert len(store.summaries()) == 3
```

Run: `pytest tests/test_store.py -v`
Expected: FAIL (`No module named 'materialsdb.store'`)

- [ ] **Step 2: Implement store.py**

Create `src/materialsdb/store.py` exactly as below:

```python
import datetime
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from lxml import etree, objectify

from materialsdb import cache, config
from materialsdb.classes import Material
from materialsdb.serialiser import XmlDeserialiser, get_valid_root
from materialsdb.summary import MaterialSummary, summarize_material

Report = cache.Report

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id TEXT PRIMARY KEY, company_id TEXT, company TEXT, category TEXT,
    names TEXT, descriptions TEXT,
    lambda_min REAL, lambda_max REAL, thick_min REAL, thick_max REAL,
    usage TEXT, source_file TEXT, xml BLOB);
CREATE INDEX IF NOT EXISTS idx_company ON materials(company);
CREATE INDEX IF NOT EXISTS idx_category ON materials(category);
CREATE INDEX IF NOT EXISTS idx_lambda ON materials(lambda_min);
CREATE TABLE IF NOT EXISTS producer_files (
    path TEXT PRIMARY KEY, sha256 TEXT, built_at REAL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_NUMERIC_SORTS = {"lambda": "lambda_min", "thick": "thick_min"}
_STRING_SORTS = {"company": "company", "category": "category"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MaterialStore:
    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = (
            Path(db_path) if db_path else cache.get_cache_folder() / "materials.db"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.db_path))
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(_SCHEMA)
        self._ensure_schema_version()

    # ---------- meta / lifecycle ----------

    def _ensure_schema_version(self):
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        stored = row[0] if row else None
        if stored != SCHEMA_VERSION:
            self.connection.execute("DELETE FROM materials")
            self.connection.execute("DELETE FROM producer_files")
            self.connection.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )
            self.connection.commit()

    def close(self):
        self.connection.close()

    # ---------- build / refresh ----------

    def refresh(self, force=False, paths=None) -> Report:
        if paths is None:
            paths = list(cache.producers())
        else:
            paths = [Path(p) for p in paths]

        existing, updated = [], []
        deserialiser = XmlDeserialiser()
        for path in paths:
            digest = _sha256(path)
            row = self.connection.execute(
                "SELECT sha256 FROM producer_files WHERE path=?", (str(path),)
            ).fetchone()
            if not force and row and row[0] == digest:
                existing.append(path)
                continue
            try:
                self._upsert_file(deserialiser, path)
            except Exception as err:
                print(f"{path.name}: skipped during store refresh:\n\t{err}")
                continue
            self.connection.execute(
                "INSERT INTO producer_files(path, sha256, built_at) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, "
                "built_at=excluded.built_at",
                (str(path), digest, datetime.datetime.now().timestamp()),
            )
            updated.append(path)

        kept = {str(p) for p in paths}
        deleted = []
        for (stored_path,) in self.connection.execute(
            "SELECT path FROM producer_files"
        ).fetchall():
            if stored_path not in kept:
                self.connection.execute(
                    "DELETE FROM materials WHERE source_file=?", (stored_path,)
                )
                self.connection.execute(
                    "DELETE FROM producer_files WHERE path=?", (stored_path,)
                )
                deleted.append(Path(stored_path))
        self.connection.commit()
        return Report(existing, updated, deleted)

    def _upsert_file(self, deserialiser: XmlDeserialiser, path: Path):
        tree = objectify.parse(str(path))
        root = get_valid_root(tree)
        source = deserialiser.from_element(root)
        self.connection.execute(
            "DELETE FROM materials WHERE source_file=?", (str(path),)
        )
        for element in root.material:
            material = deserialiser.from_element(element)
            summary = summarize_material(
                material, company_id=str(source.companyid), company=source.company
            )
            self.connection.execute(
                "INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    summary.id,
                    summary.company_id,
                    summary.company,
                    summary.category,
                    json.dumps(summary.names),
                    json.dumps(summary.descriptions),
                    summary.lambda_min,
                    summary.lambda_max,
                    summary.thick_min,
                    summary.thick_max,
                    json.dumps(summary.usage),
                    str(path),
                    sqlite3.Binary(etree.tostring(element)),
                ),
            )

    # ---------- queries ----------

    def summaries(
        self,
        company=None,
        category=None,
        min_lambda=None,
        max_lambda=None,
        min_thick=None,
        max_thick=None,
        usage=None,
        text=None,
        sort="company",
        ascending=True,
        lang=None,
    ) -> List[MaterialSummary]:
        where, params = [], []

        def add(condition, value):
            where.append(condition)
            params.append(value)

        if company:
            add("company=?", company)
        if category:
            add("category=?", category)
        if min_lambda is not None:
            add("lambda_max>=?", min_lambda)
        if max_lambda is not None:
            add("lambda_min<=?", max_lambda)
        if min_thick is not None:
            add("thick_max>=?", min_thick)
        if max_thick is not None:
            add("thick_min<=?", max_thick)

        query = "SELECT * FROM materials"
        if where:
            query += " WHERE " + " AND ".join(where)
        rows = self.connection.execute(query, params).fetchall()

        lang = lang or config.get_lang()
        results = [self._row_to_summary(row) for row in rows]

        if usage:
            results = [r for r in results if r.usage.get(usage)]
        if text:
            needle = text.lower()
            results = [
                r
                for r in results
                if needle in (r.names.get(lang) or r.names.get("") or "").lower()
            ]
        return self._sorted(results, sort, ascending)

    @staticmethod
    def _sorted(
        results: List[MaterialSummary], sort: str, ascending: bool
    ) -> List[MaterialSummary]:
        reverse = not ascending
        if sort == "name":
            return sorted(
                results,
                key=lambda r: r.names.get("") or "",
                reverse=reverse,
            )
        if sort in _NUMERIC_SORTS:
            attr = _NUMERIC_SORTS[sort]
            return sorted(
                results,
                key=lambda r: (
                    getattr(r, attr) is None,
                    getattr(r, attr) if getattr(r, attr) is not None else 0,
                ),
                reverse=reverse,
            )
        attr = _STRING_SORTS.get(sort, "company")
        return sorted(results, key=lambda r: str(getattr(r, attr)), reverse=reverse)

    @staticmethod
    def _row_to_summary(row) -> MaterialSummary:
        (
            id_,
            company_id,
            company,
            category,
            names,
            descriptions,
            lambda_min,
            lambda_max,
            thick_min,
            thick_max,
            usage,
            _source_file,
            _xml,
        ) = row
        return MaterialSummary(
            id=id_,
            company_id=company_id,
            company=company,
            category=category,
            names=json.loads(names),
            descriptions=json.loads(descriptions),
            lambda_min=lambda_min,
            lambda_max=lambda_max,
            thick_min=thick_min,
            thick_max=thick_max,
            usage=json.loads(usage),
        )

    def get(self, material_id: str) -> Optional[Material]:
        row = self.connection.execute(
            "SELECT xml FROM materials WHERE id=?", (material_id,)
        ).fetchone()
        if row is None:
            return None
        element = objectify.fromstring(bytes(row[0]))
        return XmlDeserialiser().from_element(element)
```

Implementation notes (already applied in the code above — listed so the implementer understands the choices):
- Numeric range filters follow intersection semantics (`min_lambda` compares against `lambda_max`, `max_lambda` against `lambda_min`, same pattern for thickness) so a material whose range straddles the bound still matches.
- Sorting: NULLs last via tuple keys for numeric columns; simple string keys otherwise; `sort="name"` sorts on the language-independent `""` entry.
- Fragments stored via `etree.tostring(element)` — objectify elements are lxml elements, so no second parse is needed.
- One caveat to verify while testing: `sort` with `reverse=True` puts non-null values descending but keeps NULL placement inverted; acceptable v1 behavior, noted rather than handled.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_store.py -v`
Expected: PASS (10 tests). Iterate on failures until green.

- [ ] **Step 4: Run full suite**

Run: `pytest`
Expected: all non-benchmark tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/store.py tests/test_store.py
git commit -m "feat: sqlite material store with incremental per-producer refresh"
```

---

### Task 7: Rewrite `query.py` as store facade

**Files:**
- Modify: `src/materialsdb/query.py` (full rewrite)
- Create: `tests/test_query.py`

**Interfaces:**
- Consumes: `store.MaterialStore`
- Produces:

```python
get_store() -> MaterialStore                       # lazily opened, lru_cached singleton
get_material(material_id: str) -> Optional[Material]
search(text: str, **filters) -> List[MaterialSummary]   # summaries(text=text, **filters)
refresh(force: bool = False) -> Report
```

Old `SOURCES` / `get_by_name` / `get_by_producer` are deleted (break approved).

- [ ] **Step 1: Write failing test**

Create `tests/test_query.py`:

```python
import pytest

from materialsdb import query
from materialsdb.store import MaterialStore


@pytest.fixture
def isolated_store(tmp_path, monkeypatch, mini_xml):
    query.get_store.cache_clear()
    monkeypatch.setattr(
        "materialsdb.query.MaterialStore",
        lambda: MaterialStore(db_path=tmp_path / "q.db"),
    )
    store = query.get_store()
    store.refresh(paths=[mini_xml])
    yield store
    store.close()
    query.get_store.cache_clear()


def test_get_material(isolated_store):
    material = query.get_material("00000000-0000-0000-0000-000000000002")
    assert material.information.group == "Concrete"


def test_search(isolated_store):
    rows = query.search("beton")
    assert len(rows) == 1
```

Note: monkeypatching `materialsdb.query.MaterialStore` works because `query.py` binds the name via its own import; `cache_clear()` prevents cross-test pollution of the singleton.

Run: `pytest tests/test_query.py -v`
Expected: FAIL (old query.py exposes none of `get_store`/`get_material`/`search`)

- [ ] **Step 2: Implement**

Replace the entire contents of `src/materialsdb/query.py`:

```python
"""Convenience facade over the SQLite material store."""
from functools import lru_cache
from typing import List, Optional

from materialsdb.classes import Material
from materialsdb.store import MaterialStore, Report
from materialsdb.summary import MaterialSummary


@lru_cache(maxsize=1)
def get_store() -> MaterialStore:
    return MaterialStore()


def get_material(material_id: str) -> Optional[Material]:
    return get_store().get(material_id)


def search(text: str, **filters) -> List[MaterialSummary]:
    return get_store().summaries(text=text, **filters)


def refresh(force: bool = False) -> Report:
    return get_store().refresh(force=force)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_query.py tests/test_store.py tests/test_summary.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/materialsdb/query.py tests/test_query.py
git commit -m "feat!: rewrite query module as facade over sqlite store"
```

---

### Task 8: Store benchmarks, README docs, final measurement

**Files:**
- Modify: `tests/benchmarks/test_parse_benchmarks.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `MaterialStore`, fixture constants defined in Task 2's benchmark file
- Produces: documented public API + recorded speedups. Decision gate: if parse benchmarks remain slow after Tasks 3–4, report numbers to the maintainer before considering the spec's optional "replace objectify with plain etree" step — it is explicitly out of scope unless measurements justify it.

- [ ] **Step 1: Append store benchmarks**

Append to `tests/benchmarks/test_parse_benchmarks.py`:

```python
@pytest.fixture(scope="module")
def populated_store(tmp_path_factory):
    from materialsdb.store import MaterialStore

    store = MaterialStore(db_path=tmp_path_factory.mktemp("bench") / "bench.db")
    store.refresh(paths=[MINI_XML])
    yield store
    store.close()


def test_store_rebuild(benchmark, populated_store, tmp_path_factory):
    benchmark(populated_store.refresh, force=True, paths=[MINI_XML])


def test_summaries_sorted_by_lambda(benchmark, populated_store):
    benchmark(populated_store.summaries, sort="lambda")


def test_get_single_material(benchmark, populated_store):
    benchmark(populated_store.get, "00000000-0000-0000-0000-000000000001")
```

- [ ] **Step 2: Run and compare against baselines**

Run: `pytest -m benchmark --benchmark-compare=<N>_baseline` (same `<N>` resolution as Task 3; alternatively run `pytest -m benchmark` and compare means against the Task 2 records manually)
Expected: parse benchmarks improved vs baseline; `summaries` sort and single-material `get` in the millisecond range. Paste the comparison table into the commit message body.

- [ ] **Step 3: Document the query API in README.md**

Insert a new section after the existing `# Usage examples` section (before `# How to install`):

````markdown
# Querying materials :
The library keeps an sqlite index of the cached materials data for fast
filtering and single-material access:

```python
from materialsdb import query

query.refresh()                                   # incremental update from cached xml
rows = query.search("isolant", sort="lambda")     # filtered, sorted summaries
material = query.get_material(rows[0].id)         # full material dataclass
```
````

- [ ] **Step 4: Full verification**

Run:

```bash
pytest && black src/materialsdb/store.py src/materialsdb/summary.py src/materialsdb/query.py src/materialsdb/serialiser.py tests/
```

Expected: tests PASS; black reformats any drift (then rerun `pytest` to confirm still green).

- [ ] **Step 5: Commit**

```bash
git add tests/benchmarks/test_parse_benchmarks.py README.md
git commit -m "docs+bench: document query api, add store benchmarks, record speedups"
```
