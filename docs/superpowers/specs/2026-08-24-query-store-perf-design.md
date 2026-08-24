# Design: Indexed query layer + performance core

Date: 2026-08-24
Status: Approved (brainstorming session, sub-project 2 of 5)

## Context & goals

python-materialsdb currently deserialises every producer XML fully and keeps no
queryable index (~46 producers, ~2,300 materials, ~12 MB XML in the user cache).
Known bottlenecks: `typing.get_type_hints()` re-evaluated per XML element in
`XmlDeserialiser.from_element` (`src/materialsdb/serialiser.py`), sequential
parsing of producers, O(n²) style lookups in IFC generation.

Goals:

1. Near-instant single-material fetch by materialsdb id.
2. Fast sorted/filtered listing of all materials (by company, category, lambda,
   thickness, usage flags) to power a future cross-platform GUI picker.
3. Measurable parsing speedups, benchmark-driven.

Non-goals: GUI itself (sub-project 4), single-material IFC export
(sub-project 3), ruff/ty tooling migration (sub-project 1), multilanguage
improvements beyond what this schema enables.

## 1. Benchmark-first

Add `tests/benchmarks/` using `pytest-benchmark`, run via `pytest -m benchmark`
(excluded from default test run). Baselines committed before any optimization:

- parse `example_v103.xml`
- parse largest cached producer file (skipped if cache absent)
- parse all cached producers sequentially (skipped if cache absent)
- store build + representative queries: `summaries()` sorted by lambda, `get(id)`

Every optimization step must re-run benchmarks; only measured wins are kept.

## 2. Serialiser optimizations

Applied in order, re-benchmarking after each:

1. Cache `typing.get_type_hints()` results per class.
2. Parse producers in parallel with `ThreadPoolExecutor` (lxml parsing releases
   the GIL).
3. Only if benchmarks still demand it: replace `objectify` attribute access with
   plain `etree` accessors in hot paths.

## 3. Storage & components

```
src/materialsdb/
├── store.py     # NEW: SQLite store — build, refresh, query API
├── summary.py   # NEW: MaterialSummary dataclass + extraction rules
├── serialiser.py  # optimized; gains single-material fragment entry point
├── query.py     # rewritten as thin facade over store
└── cache.py     # unchanged
```

Database: stdlib sqlite3 at `<cache>/materials.db` (WAL mode).

```sql
materials(
  id TEXT PRIMARY KEY,          -- materialsdb material id
  company_id TEXT, company TEXT,
  category TEXT,
  names JSON, descriptions JSON,-- ALL languages stored
  lambda_min REAL, lambda_max REAL,
  thick_min REAL, thick_max REAL,
  usage JSON,                   -- wall / roof / floor / door flags
  xml BLOB                      -- raw <Material> element for lazy detail load
);
meta(schema_version INT, built_at INT, producer freshness stamps);
-- indexes: company, category, lambda_min
```

Decisions baked in:

- All languages stored → language switches via `config.get_lang()` need no
  rebuild.
- Lambda/thickness summaries aggregate min/max across layers for the configured
  country (geometry variants are country-specific).
- Incremental refresh: per-producer upsert keyed on ProducerIndex freshness;
  updating one supplier never rebuilds the others.

## 4. Query API

```python
db = MaterialStore()  # opens db; auto-refreshes stale producers

db.summaries(company=..., category=..., min_lambda=..., max_lambda=...,
             min_thick=..., max_thick=..., usage="wall", text="iso",
             sort="lambda" | "company" | "category" | "thick",
             ascending=True) -> list[MaterialSummary]

db.get(material_id) -> Material | None  # lazy: xml BLOB -> fragment deserialisation
db.refresh(force=False) -> Report       # incremental per-producer update
```

`query.py` becomes ~15 lines of convenience wrappers over a lazily-opened
module-level store; the module-global `SOURCES` singleton is removed (API break
approved — library is pre-1.0). `cache.py` unchanged.

## 5. Error handling

- Corrupt producer XML → skip that producer, include it in the returned
  `Report`; a build never aborts because of one bad file.
- Missing lambda/thickness values → NULL columns; sorts place them last.
- `meta.schema_version` mismatch → transparent full rebuild.
- WAL mode so GUI and scripts can read concurrently during refresh.

## 6. Testing

- Small synthetic fixtures under `tests/fixtures/`, **committed to git** — ends
  the dependency on the untracked 728 KB `example_v103.xml`.
- Equivalence tests: store query results match direct deserialisation of the
  same material from its source XML.
- Existing serialise/deserialise roundtrip test retained.
- Benchmarks excluded from default `pytest` invocation.

## Future hooks

Sub-project 3 (single-material IFC creation) and sub-project 4 (GUI material
picker) both consume `MaterialStore` unchanged; neither needs to touch parsing.
