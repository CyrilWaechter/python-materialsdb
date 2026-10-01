# Design: Colour scheme theming — named palettes, editor, model recolour

Date: 2026-10-01
Status: Approved (brainstorming session)

## Goals

Today surface styles are named `color <int>` when a material carries a producer
colour, else `category <Category>` coloured from the single hard-coded Lesosai
palette (`material_builder.CATEGORIES`). The `scheme` parameter of `style_for`
and the style methods of `MaterialBuilder` exist but no real caller ever passes
it (`gui/listener.py` and `ifc/project_library.py` call without it).

This feature makes the palette a first-class preference:

- a **colour scheme** preference (active named palette) honoured by every path
  that creates materialsdb styles: picker pushes, construction pushes,
  project-library and batch exports;
- an **ignore producer colour** preference that uses the scheme's category
  colour everywhere, instead of letting a producer colour win;
- a **standalone Settings page** with a palette editor: clone a palette, edit
  its 17 category colours with colour pickers and a live preview, save under a
  name, rename/delete custom palettes, export/import them;
- a **restyle action** ("Apply colours to model") that repaints the materialsdb
  materials of the open Bonsai model with the active palette, touching nothing
  but surface styles;
- an add-on fix so a **reused style's colour is refreshed** when it changed,
  which is what makes a palette switch visible on re-push.

Hatch/pattern rendering stays out of scope; see *Out of scope*.

## Decisions

- **Built-ins in code, custom palettes in user data.** Lesosai stays a code
  constant and is read-only (clone to edit). Custom palettes live in
  `schemes.json`, next to `config.json`; `config.json` keeps preferences only.
- **Full palettes only.** Every palette always holds all 17 category colours;
  writes and imports are validated (integer 0–255, known categories, name
  rules, built-in names reserved).
- **Versioned envelope for transfer.** Export/import uses
  `{"format": "materialsdb-scheme/1", "name": ..., "categories": {...}}`;
  import never silently overwrites an existing name (`409`; the dialog lets you
  rename and retry).
- **Fallbacks are forgiving.** An active scheme that no longer exists resolves
  to Lesosai; an unknown material category resolves to `Others` (existing
  behaviour). A corrupt `schemes.json` never breaks a push: built-ins are used
  and the Settings page shows a warning.
- **Schemes are cosmetic, never versioned data.** The colour scheme is not part
  of the fingerprint or the identity pset, and changing it marks nothing stale.
  Existing models are recoloured only by the explicit restyle action (or by a
  later update push, which already refreshes styles).
- **Restyle is style-only.** No psets, identity, layers, thicknesses or push
  snapshots are touched. Materials with no style or only materialsdb-created
  styles are refreshed; foreign/manual styles are preserved and counted.
- **Settings becomes a real page.** The hidden per-page Settings panel is
  removed; all settings (including language/country) live on
  `/settings.html`.
- **Style names are unchanged:** `category <Category>` for scheme colours,
  `color <int>` for producer colours — at most one style name per source, as
  today.

## Architecture

```
settings.js ──GET/POST /api/schemes, /api/config──▶ server ──schemes.json (palettes)──
             ──POST /api/model/restyle────────────▶       ──config.json (preferences)──
                                                          │
Bonsai add-on ◀── {"action": "restyle", materials: [...]}─┘  (via /api/listener poll)
             ──▶ applies styles only, reports via /api/listener/status
```

- **`materialsdb.schemes`** (new module) owns palettes: load, validate, merge
  with built-ins, CRUD, import/export.
- **The server** resolves styles (store material colour/category + active
  preferences) and queues payloads; it never edits the IFC file.
- **The add-on** applies style-only changes to the open model and refreshes the
  linked Bonsai Blender materials.

## Data model

**New module `src/materialsdb/schemes.py`.** The palette domain, free of
`ifcopenshell`:

- built-in palettes: `BUILTIN = {"Lesosai": CATEGORIES}` — the historical 17
  entries, keeping the existing `CategoryStyle` shape
  (`{"hatch": "", "color": [r, g, b]}`) so `CATEGORIES`/`SCHEMES` re-exports
  stay value-compatible and `hatch` remains a reserved field for the future
  pattern spec;
- `DEFAULT_SCHEME = "Lesosai"`;
- `available() -> dict[str, dict[str, CategoryStyle]]` — built-ins ⊕ custom;
  custom entries cannot shadow built-ins; a missing/corrupt `schemes.json`
  yields built-ins only;
- `get(name) -> dict | None`;
- `save(name, categories)`, `rename(old, new)`, `delete(name)` — custom only,
  each validating first and writing atomically (temp file + `os.replace`);
- `validate(name, categories)` — raises `SchemeError` with a precise message;
- `export_payload(name) -> dict`, `import_payload(data) -> str` (validates and
  saves, returns the imported name).

**Name rules:** trimmed, 1–64 characters, no `/` (URL routing), no control
characters; must not equal a built-in name or the reserved word `import` (the
route `POST /api/schemes/import`); names travel URL-encoded and are `unquote`d
server-side.

**`schemes.json`** (in `config.get_config_dir()`):

```json
{"version": 1, "schemes": {"MonPalette": {"Others": {"hatch": "", "color": [255, 255, 255]}}}}
```

`available()` tolerates version skew: unknown top-level keys are ignored; a
non-list / out-of-range `color` makes the whole file read as "no custom
palettes" and set a load error the server surfaces.

**`config.json`** gains:

- `"scheme"`: active palette name (unset → `DEFAULT_SCHEME`; the getter returns
  `""` so resolution falls back, and `GET /api/config` reports the effective
  name);
- `"ignore_producer_color"`: stored `"true"`/`"false"` (getter also accepts a
  hand-edited JSON bool and `1/yes/on`).

**Public-name compatibility:** `CATEGORIES`, `SCHEMES`, `DEFAULT_SCHEME` remain
importable from `material_builder` (re-exported from `schemes`), so
`project_library`'s historical re-exports keep working.

## Style resolution

`style_for(color, category, scheme=None, ignore_producer_color=False)`:

```python
if color and not ignore_producer_color:
    return f"color {int(color)}", _rgb_int_components(int(color))
palette = schemes.available().get(scheme or schemes.DEFAULT_SCHEME)
if palette is None:
    palette = schemes.available()[schemes.DEFAULT_SCHEME]
category = category if category in palette else "Others"
return f"category {category}", tuple(component / 255 for component in palette[category])
```

`MaterialBuilder.__init__(..., style_scheme=None, ignore_producer_color=None)`
defaults both from config (`style_scheme=None` → `config.get_scheme()`;
`None` → `config.get_ignore_producer_color()`), so every export path follows
the preference; explicit arguments still win. The parameter is `style_scheme`
because `build(scheme=)` already means the *fingerprint* scheme.
`_add_style_chain`/`get_surface_style` accept an optional
`ignore_producer_color` and forward the builder's values by default.

`gui/listener.py`'s two `style_for` call sites (materials and construction
layers) pass `scheme=config.get_scheme(), ignore_producer_color=config.get_ignore_producer_color()` —
read live at push time, so a settings change applies to the next push without a
server restart.

## Server API

All endpoints use the existing request routing and token/auth conventions.

| Endpoint | Body / params | Result |
|---|---|---|
| `GET /api/schemes` | — | `{"schemes": [{"name", "builtin"}], "error"?: str}` |
| `GET /api/schemes/<name>` | — | `{"name", "builtin", "categories": {...}}`; 404 unknown |
| `POST /api/schemes/<name>` | `{"categories": {...}}` | create/overwrite **custom**; 400 built-in or invalid |
| `POST /api/schemes/<name>/rename` | `{"new_name": ...}` | custom only; 400 invalid/collision; active preference follows the rename |
| `POST /api/schemes/<name>/delete` | — | custom only; 404 unknown; active preference resets to Lesosai |
| `GET /api/schemes/<name>/export` | — | the `materialsdb-scheme/1` envelope |
| `POST /api/schemes/import` | envelope | 400 invalid; 409 name exists |
| `GET /api/config` | — | adds `scheme` (effective), `schemes` (names), `ignore_producer_color` |
| `POST /api/config` | `lang`, `country`, `scheme`, `ignore_producer_color` | `scheme` validated against `available()` → 400 otherwise |
| `POST /api/model/restyle` | `{}` | 409 no active model or no listener; else `{"queued": n, "skipped": [...], "scheme": name}` |

**Restyle planning** (`_restyle_plan(store_, model_path)`): for every entry of
`store_.get_model_materials(model_path)` — all materialsdb materials, regardless
of `used_in` — resolve the store material and build
`{"material_id", "style_name", "style_color"}` via `style_for(material.information.color,
information.group, scheme, ignore)`. Materials whose id no longer exists in the
store are `skipped`. The payload is queued as
`{"action": "restyle", "materials": [...]}`; unlike force update it writes no
`record_pushed_materials` snapshot and no fingerprints.

## Settings page and palette editor

**New `static/settings.html` + `static/settings.js`** (vanilla JS, same style
as the other pages). The top nav on all pages becomes
`Materials | Constructions | Settings` with a real link and active state; the
hidden `#settings-panel` is removed from `index.html` and
`constructions.html`. `app.js`/`app-constructions.js` stop touching
`#lang`/`#country`; both keep reading `/api/config` for
`document.documentElement.lang`.

Sections:

1. **General** — Language and Country selects; `POST /api/config`.
2. **Colour scheme** —
   - active scheme `<select>` from `GET /api/schemes` (built-ins marked);
   - checkbox “Always use category colours (ignore producer colours)”;
   - **Apply colours to model** button → `POST /api/model/restyle`; disabled
     with a hint when no model/listener is connected; renders the queued and
     skipped counts and refreshes the listener status afterwards.
3. **Palettes** —
   - scheme list (built-ins flagged read-only) with **Clone**, **Rename**,
     **Delete**, **Export**, **Import**;
   - editor for the selected custom scheme: 17 rows of category name +
     `<input type="color">` + synced hex field;
   - **live preview**: a colour chip per row plus a small sample build-up
     (render / masonry / insulation / timber / concrete slab) drawn with plain
     CSS divs and re-rendered on every change;
   - **Save** updates the selected custom scheme, **Save as…** creates a new
     one (inline error on conflict); built-ins are edited by cloning.
4. **Import/export** — Export downloads `<name>.json` (Blob from the export
   endpoint); Import reads a file, prefills its name in a dialog, and on `409`
   shows the conflict so the name can be changed before retrying.

The page keeps an in-memory draft of the selected palette with a dirty marker;
leaving with unsaved changes is guarded (`beforeunload`).

## Restyle action (Apply colours to model)

**Semantics.** The button is the opt-in: it repaints every materialsdb material
known in the open model with the current resolution rules (producer colour wins
unless the override is on). It never rewrites psets, identity, layer
thicknesses, `materialsdb.org_layer` or push snapshots. Foreign/manual styles
are kept; the add-on counts them (`kept_styles`). Materials absent from the
store are reported as skipped by the server (nothing to resolve).

**Add-on (`bonsai_addon/insert.py` + `__init__.py`):**

- `_apply_style` reuse branch: when an existing `IfcSurfaceStyle` is reused by
  name, compare its `IfcSurfaceStyleShading.SurfaceColour` with the payload and
  update the RGB in place when different. This makes palette switches (whose
  style name does not change) take effect on re-push, and makes the existing
  force update repaint as a side effect.
- New `apply_restyle(file, payload, summary=None)`:
  for each `{material_id, style_name, style_color}` find **all** `IfcMaterial`
  entities whose identity pset carries that id (a multi-layer material can have
  several), refresh only when `_should_style(material)` (else
  `summary["kept_styles"]`), and call `_apply_style`. Ids with no material land
  in `summary["missing"]`. Returns the number restyled.
- Dispatch in the push operator: `action == "restyle"` → apply, then
  `_sync_style_materials`, then report
  `"{n} material(s) restyled"` plus kept/missing counts through
  `/api/listener/status`; `_undo_label` names the action. Unknown actions
  already report an error, so an older add-on fails loudly rather than
  silently.
- **Blender viewport refresh:** the restyle path re-runs
  `_sync_style_materials`; if `tool.Style.switch_shading` does not re-read the
  updated IFC colour, it is extended to reapply the shading colour to the
  linked Blender material. Verified by the local in-Blender harness.

## Error handling

- Invalid palette writes/imports → `400` with the failing category/value; the
  editor shows it inline and keeps the draft.
- Unknown scheme in `POST /api/config` → `400`; unknown/corrupt active scheme at
  read time → Lesosai.
- Corrupt `schemes.json` → built-ins only plus an `error` field on
  `GET /api/schemes`; it is never rewritten implicitly.
- Import name conflict → `409`; renaming to a built-in name → `400`.
- Restyle without an active model or connected listener → `409`; the button
  shows the hint.
- Concurrent tabs: last write wins (single-user tool); no locking.

## Testing

- **`tests/test_schemes.py`** (new): validate/save/rename/delete round-trips,
  reserved built-in names, invalid colours/categories, atomic write, corrupt
  file → built-ins + error, import/export envelope, fallback of a missing
  active scheme.
- **`tests/test_material_builder.py`**: `style_for` with a custom scheme and
  with `ignore_producer_color=True`; builder defaults read config, explicit
  arguments win.
- **`tests/test_listener_payload.py`**: material and construction payloads
  under a custom scheme + override.
- **`tests/test_gui_server.py`**: schemes CRUD/validation/409, extended
  `GET/POST /api/config`, `_restyle_plan` (skipped materials, style resolution)
  and the restyle endpoint (queue, 409 without listener).
- **`tests/test_bonsai_insert.py`**: `_apply_style` recolours a reused style;
  `apply_restyle` refreshes ours, keeps foreign styles, reports missing ids.
- **`tests/test_settings_frontend.py`** + `tests/harness/settings_page_harness.mjs`
  (new): selector population, override POST, editor → save payload, preview
  re-render, force button disabled state. Existing harnesses are updated for
  the removed per-page settings handlers.
- **In-Blender harness** (local-only, as usual): restyle refreshes the viewport
  colour of an already-pushed model.
- **Gates:** full pytest, `ruff check`, `ruff format --check`, `ty`; rebuild
  `dist/materialsdb_listener-<ver>.zip`. No release until the user asks.

## Compatibility

- Public names `CATEGORIES`, `SCHEMES`, `DEFAULT_SCHEME` remain importable from
  `material_builder`; `project_library` re-exports are unchanged.
- Payload additions are additive; `restyle` is a new action, and an old add-on
  reports “unknown action” rather than misapplying it. Server and add-on are
  shipped together (zip rebuild), so this is a migration note only.
- Style names and the identity pset are untouched; no store schema change and
  no cache wipe.

## Out of scope

- **Hatch/pattern rendering** (applying the reserved `hatch` field: IFC pattern
  representation, Bonsai display) — needs its own spec; remains in the backlog.
- Automatic recolouring on scheme change, page load or model open.
- Per-material colour overrides in the picker or editor (palettes are edited as
  a whole).
- Editing or deleting built-in palettes in place.
- Sharing/syncing palettes across machines (export/import is manual).
- 2D annotation/drawing colours.
