# Design: Web-UI material picker (`materialsdb-gui`)

Date: 2026-08-25
Status: Approved (brainstorming session, sub-project 4 of 5)

## Goals

Standalone cross-platform picker: explore all cached materials (sort/filter/
search), inspect one in detail, and hand materials to BIM software — export a
standalone `.ifc` or append into a user-chosen `.ifc`. Doubles as an HTTP
service so future host plugins (Bonsai/FreeCAD/Revit listeners) can drive the
same operations programmatically.

Non-goals: embedding inside host UIs (host plugins call our Python API),
construction maker (layer-set composition and U-value computation from the
encoded `<construction>` stacks — its own future sub-project), multi-user or
LAN serving.

## Decisions

- **Toolkit: local web UI as service.** Stdlib HTTP server plus a vanilla JS
  page; zero new runtime dependencies; the GUI *is* the future plugin API.
  Chosen over PySide6 because host integration always flows through our core
  API over HTTP anyway, and Bonsai already embraces web UIs internally.
- **Standalone-first deployment** via a `materialsdb-gui` console script.
- **Multi-select batches** for export/append.
- **Type-aware display** for `simple` / `btk` / `construction`. Schema fact:
  a material carries exactly one of `layers` | `variations` | `construction`;
  `construction` bodies are text-only (`consref`, `designusage`, encoded stack
  string). Local census: 2,184 simple / 106 btk / 43 construction.

## Architecture

```
src/materialsdb/gui/
├── __main__.py     # args (--port, --no-browser), server start, webbrowser.open
├── server.py       # ThreadingHTTPServer + tiny router (~250 lines, stdlib only)
└── static/
    ├── index.html
    └── app.js      # vanilla JS fetch() table + detail pane (~300 lines)
```

- `[project.scripts] materialsdb-gui = "materialsdb.gui.__main__:main"`.
- Packaging data gains `*.html` and `*.js` patterns.
- Server binds `127.0.0.1` only. Every mutating endpoint requires an
  `X-MaterialsDB-Token` header carrying a random per-launch token that is
  injected into the served page — random websites cannot CSRF your localhost.
- Core stays GUI-free: gui imports query/store/material_builder; never the
  reverse.

## API surface

| Endpoint | Purpose |
|---|---|
| `GET /` | serves index.html with injected token |
| `GET /api/materials?type=&company=&category=&min_lambda=&max_lambda=&text=&sort=&order=` | JSON list of `summaries()` |
| `GET /api/materials/{id}` | full detail: names (all langs), descriptions, metrics, btk U-ranges, consref/designusage |
| `POST /api/export` `{ids[]}` | standalone multi-material .ifc download |
| `POST /api/session/open` `{path}` / `POST /api/pick` `{ids[], replace}` / `POST /api/session/save` `{path?}` | append workflow into a chosen .ifc (one open session) |
| `POST /api/config` `{lang, country}` | live language switch (re-query, no restart) |
| `POST /api/refresh` | incremental cache+store update, returns Report |

Table columns: name · company · category · λ range · thickness range · usage
flags · type.

## Type-aware summaries (small core extension)

- `MaterialSummary.type` field added; store schema unchanged (JSON columns).
- `btk`: thickness range from `variations.vgeometry`; λ stays NULL by design —
  `vthermal` carries assembly U-values (different physics); the detail pane
  shows U-value ranges instead.
- `construction`: all metrics NULL by schema nature; detail pane shows
  `consref` and `designusage`.

## Semantics & error handling

Append sessions reuse `add_material(..., replace=)` including purge semantics;
save-as supported. Unknown id → 404 JSON; bad path → 400; pick with no open
session → 409; token mismatch → 403. Known accepted limitation: repeated
replace cycles accumulate style-chain entities (deferred backlog item from
sub-project 3).

## Testing

pytest drives the real server on an ephemeral port via `http.client`: listing
and filters, export → reopen via ifcopenshell, session open → pick → save →
reopen, 403/404/409 paths, config-switch effect on returned names. Frontend JS
is untested in v1 (stated limitation). Tooling gates stay green; CI unchanged.
