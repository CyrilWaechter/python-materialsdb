# Backlog

Candidate work items, deliberately not spec'd yet. Add new entries at the
bottom; move an entry to `docs/superpowers/specs/` when it graduates.

## Material update versioning

**Date opened:** 2026-09-12 (pushed materials are never auto-updated —
maintainer decision, keep it that way)

An IFC model holds a frozen copy of the material data it was pushed with.
Materialsdb material GUIDs are stable identifiers, and producers do not
necessarily regenerate a GUID when they change a material's data — the only
versioned levels are the producer file (`ver`/`crd`) and the index
(`LastKnownDate`/`KnownVersion`). Consequences today:

- a producer updating a material's data (same GUID) makes a re-push a
  **silent no-op**: the model keeps stale properties with no warning;
- a producer regenerating the GUID leaves the stale material in the model
  next to the new one (manual purge needed).

**Candidate fix (undecided):** at push time store a small data fingerprint
(hash of the material's XML definition, or `ver`+hash) in the identity
property set. On re-push compare: identical fingerprint → verifiable no-op;
different → either *warn + skip* (user purges manually) or *warn + opt-in
in-place update* (overwrite only what we own: our property sets, styles,
name/category; foreign psets and user edits untouched). A GUI report
"materials changed since your last push" (driven by producer `ver` we
already detect) could be the softer middle ground.

## Parallel store ingest

**Date opened:** 2026-09-12

The download half of *Refresh cache* now runs on a bounded thread pool
(`cache.update_producers_data`, v0.3.0+), but store ingestion is serial:
`store.refresh` parses each producer file and inserts its materials one at
a time (`MaterialStore._upsert_file`), even though the batch parser
`XmlDeserialiser.from_xml_files` already parses concurrently. Ideas to
evaluate: route refresh through the concurrent parser, and/or parallelize
`_upsert_file` — SQLite is a single writer, so inserts need batching into
the main thread or per-thread connections; the sha256 + deletion
bookkeeping must stay ordered. Benchmark against pytest-benchmark before
and after (the store suite already has benchmarks).
