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

## Ingest speed (download was parallel; profiling reshaped this item)

**Date updated:** 2026-09-12 — outcome: profiling showed the indexing phase
(11.9 s over the 46-file corpus) is GIL-bound pure-Python type
introspection in `XmlDeserialiser.from_element` (`strip_optional`,
`get_origin`/`get_args`, `re.search` per material-per-attribute), so THREAD
parallelism was a non-starter. Memoising `strip_optional`/`is_optional` and
the tag-name lookup landed instead (commit this session): **11.9 s →
≈3.9 s** (~3×). Downloads themselves were already parallel (serial
4.0 s → 0.62 s).

**Parked:** `ProcessPoolExecutor` per producer file (returns picklable
rows: summaries + `etree.tostring` bytes) could cut the remaining ~3.9 s
multi-core, at the cost of pickling/fork-safety complexity and losing the
shared objectify tree. Only revisit if the corpus grows significantly or
≈4 s becomes productively annoying. SQLite stays the single writer in that
design (batch from the main thread or per-thread connections).
