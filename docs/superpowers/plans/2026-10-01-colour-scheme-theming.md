# Colour Scheme Theming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Named colour palettes with a Settings-page editor, scheme/override preferences honoured by every style path, and a style-only "apply colours to model" action.

**Architecture:** A new `materialsdb.schemes` module owns built-in and custom palettes (`schemes.json` next to `config.json`); `style_for`/`MaterialBuilder`/the listener resolve styles from the active preferences; the server exposes scheme CRUD/export/import, extended config, and a `restyle` listener payload that the Bonsai add-on applies without touching anything but surface styles.

**Tech Stack:** Python 3.10+ (stdlib only in `schemes.py`), existing GUI HTTP server with vanilla JS static pages, ifcopenshell/ifcopenshell.api in the Bonsai add-on, pytest + a Node VM frontend harness.

**Spec:** `docs/superpowers/specs/2026-10-01-colour-scheme-theming-design.md`

## Global Constraints

- Style names are unchanged: `category <Category>` for scheme colours, `color <int>` for producer colours.
- Built-in palettes (Lesosai) are read-only; every palette holds all 17 categories; colours are integer RGB 0–255.
- `schemes.json` holds palettes, `config.json` holds preferences only; both live in `config.get_config_dir()`.
- Import/export envelope is `{"format": "materialsdb-scheme/1", "name": ..., "categories": {...}}`; import of an existing name is refused (`409`).
- Unknown active scheme resolves to Lesosai; unknown category resolves to `Others`.
- Restyle is style-only: never writes psets, identity, layers, thicknesses, fingerprints or push snapshots; foreign/manual styles are preserved.
- Settings is a standalone page; the hidden per-page Settings panel is removed.
- Python line length 120, ruff check + format, `ty` must pass. Local pytest needs `-p no:pytest-blender`; the Bonsai in-Blender harness is local-only (no `-p no:pytest-blender` for that path).
- NEVER push or release without the user's explicit go-ahead; all gates green before proposing either.

## File Structure

- Create `src/materialsdb/schemes.py` — palette domain: built-ins, custom load/save/validate, CRUD, envelope.
- Modify `src/materialsdb/config.py` — `scheme` / `ignore_producer_color` accessors.
- Modify `src/materialsdb/ifc/material_builder.py` — re-exports, `style_for` override flag, builder style defaults.
- Modify `src/materialsdb/gui/listener.py` — pass live config values to `style_for`.
- Modify `src/materialsdb/gui/server.py` — scheme API, extended config, static routes, restyle endpoint + plan.
- Create `src/materialsdb/gui/static/settings.html` + `settings.js` — standalone Settings page and palette editor.
- Modify `src/materialsdb/gui/static/index.html`, `constructions.html`, `app.js`, `app-constructions.js` — nav + panel removal.
- Modify `bonsai_addon/insert.py` — recolour-on-reuse, `apply_restyle`.
- Modify `bonsai_addon/__init__.py` — restyle dispatch, undo label, report.
- Tests: create `tests/test_schemes.py`, `tests/test_config.py`, `tests/test_settings_frontend.py`, `tests/harness/settings_page_harness.mjs`; modify `tests/test_material_builder.py`, `tests/test_listener_payload.py`, `tests/test_gui_server.py`, `tests/test_bonsai_insert.py`, `bonsai_addon/test/test_in_blender.py`.

## Review Focus

1. A corrupt or hand-edited `schemes.json` (invalid JSON, missing keys, wrong types) must never break a push or crash the server — built-ins only, error surfaced. (Tasks 1, 3)
2. Scheme names with spaces/accents must survive create → export → URL → import round-trips. (Tasks 1, 3)
3. Deleting or renaming the active scheme must reset/follow the preference, and the settings page must show the effective scheme. (Task 3, 7)
4. Restyle must keep foreign/manual styles and report store-missing materials instead of guessing. (Tasks 4, 5)
5. Re-pushing a style whose colour did not change must not touch the style, while a changed colour must be written — no false positives from float rounding. (Task 5)

---

### Task 1: Palette module and preferences

**Files:**
- Create: `src/materialsdb/schemes.py`
- Modify: `src/materialsdb/config.py` (append after `set_country`)
- Modify: `src/materialsdb/ifc/material_builder.py:1-42` (definitions move out, re-exports in)
- Create: `tests/test_schemes.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: `config.get_config_dir() -> pathlib.Path`.
- Produces (used by Tasks 2–4, 7–9):
  - `schemes.CategoryStyle` (TypedDict: `hatch: str`, `color: tuple[int, int, int]`)
  - `schemes.CATEGORIES: dict[str, CategoryStyle]` (moved verbatim), `schemes.DEFAULT_SCHEME = "Lesosai"`, `schemes.BUILTIN = {DEFAULT_SCHEME: CATEGORIES}`
  - `schemes.FORMAT = "materialsdb-scheme/1"`, `schemes.SchemeError(ValueError)`
  - `schemes.schemes_path() -> pathlib.Path`, `schemes.load_error() -> str | None`
  - `schemes.available() -> dict[str, dict[str, CategoryStyle]]` (built-ins first, custom sorted by name)
  - `schemes.get(name) -> dict[str, CategoryStyle] | None`
  - `schemes.validate(name, categories) -> None` (raises `SchemeError`)
  - `schemes.save(name, categories)`, `schemes.rename(old, new)`, `schemes.delete(name)`
  - `schemes.export_payload(name) -> dict`, `schemes.import_payload(data) -> str`
  - `config.get_scheme() -> str` (`""` when unset), `config.set_scheme(name)`
  - `config.get_ignore_producer_color() -> bool` (default False), `config.set_ignore_producer_color(value: bool)`
  - `material_builder.CATEGORIES` / `SCHEMES` / `DEFAULT_SCHEME` re-exported from `schemes` (values unchanged)

- [ ] **Step 1: Write the failing tests**

`tests/test_schemes.py`:

```python
import json

import pytest

from materialsdb import schemes


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)
    return tmp_path


def test_save_get_available_roundtrip():
    assert schemes.available() == {schemes.DEFAULT_SCHEME: schemes.CATEGORIES}
    palette = {name: dict(style) for name, style in schemes.CATEGORIES.items()}
    palette["Concrete"] = {"hatch": "", "color": [1, 2, 3]}
    schemes.save("Mon Paletté", palette)
    assert schemes.get("Mon Paletté")["Concrete"]["color"] == [1, 2, 3]
    assert list(schemes.available()) == ["Lesosai", "Mon Paletté"]
    assert json.loads((config_dir / "schemes.json").read_text())["version"] == 1


@pytest.mark.parametrize("name", ["Lesosai", "import", "", "a/b", "x" * 65])
def test_reserved_or_invalid_names_are_rejected(name):
    with pytest.raises(schemes.SchemeError):
        schemes.save(name, schemes.CATEGORIES)


@pytest.mark.parametrize(
    "categories",
    [
        {k: v for k, v in schemes.CATEGORIES.items() if k != "Concrete"},  # missing category
        {**schemes.CATEGORIES, "Nope": {"hatch": "", "color": [0, 0, 0]}},  # unknown category
        {**schemes.CATEGORIES, "Concrete": {"hatch": "", "color": [0, 0]}},  # wrong length
        {**schemes.CATEGORIES, "Concrete": {"hatch": "", "color": [0, 0, 256]}},  # out of range
        {**schemes.CATEGORIES, "Concrete": {"hatch": "", "color": [0, 0, "x"]}},  # wrong type
    ],
)
def test_validate_rejects_bad_palettes(categories):
    with pytest.raises(schemes.SchemeError):
        schemes.save("Bad", categories)


def test_rename_and_delete_custom_only():
    schemes.save("Old", schemes.CATEGORIES)
    schemes.rename("Old", "New")
    assert schemes.get("Old") is None and schemes.get("New") is not None
    schemes.delete("New")
    assert schemes.get("New") is None
    with pytest.raises(schemes.SchemeError):
        schemes.delete("Lesosai")


def test_corrupt_file_falls_back_to_builtins_with_error(config_dir):
    (config_dir / "schemes.json").write_text("{not json")
    assert schemes.available() == {schemes.DEFAULT_SCHEME: schemes.CATEGORIES}
    assert schemes.load_error()


def test_export_import_envelope_roundtrip():
    schemes.save("Mon Paletté", schemes.CATEGORIES)
    data = schemes.export_payload("Mon Paletté")
    assert data["format"] == schemes.FORMAT and data["name"] == "Mon Paletté"
    schemes.delete("Mon Paletté")
    assert schemes.import_payload(data) == "Mon Paletté"
    with pytest.raises(schemes.SchemeError):
        schemes.import_payload(data)  # existing name
    with pytest.raises(schemes.SchemeError):
        schemes.import_payload({"format": "other", "name": "X", "categories": schemes.CATEGORIES})


def test_material_builder_reexports_are_unchanged():
    from materialsdb.ifc import material_builder as mb

    assert mb.CATEGORIES is schemes.CATEGORIES
    assert mb.SCHEMES == schemes.BUILTIN
    assert mb.DEFAULT_SCHEME == "Lesosai"
```

`tests/test_config.py`:

```python
import json

import pytest

from materialsdb import config


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)
    return tmp_path


def test_scheme_defaults_empty_and_roundtrips():
    assert config.get_scheme() == ""
    config.set_scheme("MonPalette")
    assert config.get_scheme() == "MonPalette"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("false", False), (True, True), ("yes", True), ("0", False)],
)
def test_ignore_producer_color_accepts_strings_and_bools(config_dir, raw, expected):
    (config_dir / "config.json").write_text(
        json.dumps({"lang": "fr", "country": "CH", "ignore_producer_color": raw})
    )
    assert config.get_ignore_producer_color() is expected


def test_ignore_producer_color_defaults_false_and_roundtrips():
    assert config.get_ignore_producer_color() is False
    config.set_ignore_producer_color(True)
    assert config.get_ignore_producer_color() is True
    assert config.get_base_config()["ignore_producer_color"] == "true"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_schemes.py tests/test_config.py`
Expected: collection/attribute errors (`module 'materialsdb' has no attribute 'schemes'`, `get_scheme` missing).

- [ ] **Step 3: Implement `schemes.py` and the config accessors**

Move `CategoryStyle` + the 17-entry `CATEGORIES` dict verbatim from `material_builder.py` into `schemes.py`; add `DEFAULT_SCHEME`, `BUILTIN`, `FORMAT`, `RESERVED = frozenset({*BUILTIN, "import"})`, `SchemeError`.

Implementation notes:
- `mask/order`: `_validate_name(name)` rejects non-str, empty, >64 chars, `/` or any `ord(ch) < 32`, and reserved names. `validate(name, categories)` calls it, then requires `set(categories) == set(CATEGORIES)` and each entry to be a dict with `str` `hatch` (default `""`) and a 3-item list/tuple of non-bool ints 0–255.
- `_load_custom()` returns `({}, None)` when the file is missing; on any exception returns `({}, f"schemes.json ignored: {err}")` so damaged files never break callers. It re-validates each stored palette, so a file with a bad palette reads as an error + empty custom set.
- `available()` merges `BUILTIN` then custom sorted by name.
- `save` normalises (`{"hatch": str(...), "color": [int, int, int]}`), then writes atomically: dump `{"version": 1, "schemes": custom}` to `schemes_path().with_name("schemes.json.tmp")`, `os.replace` onto `schemes.json`.
- `rename` strips `new`, requires `old` custom, `new` not builtin/reserved/used, then moves the entry.
- `export_payload` / `import_payload` follow the envelope exactly; `import_payload` raises `SchemeError` when the name already exists (`available()` contains it) or validation fails.

In `config.py` append:

```python
def get_scheme() -> str:
    return str(get_base_config().get("scheme") or "")


def set_scheme(name: str) -> None:
    set_param("scheme", str(name))


def get_ignore_producer_color() -> bool:
    value = get_base_config().get("ignore_producer_color", "false")
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def set_ignore_producer_color(value: bool) -> None:
    set_param("ignore_producer_color", "true" if value else "false")
```

In `material_builder.py`, stop defining `CategoryStyle`/`CATEGORIES` and instead: `from materialsdb import config, schemes, utils`, then `CATEGORIES = schemes.CATEGORIES`, `SCHEMES = schemes.BUILTIN`, `DEFAULT_SCHEME = schemes.DEFAULT_SCHEME`, and import `CategoryStyle` for the `SCHEMES: dict[str, dict[str, CategoryStyle]]` annotation (keeps ruff happy and the public names importable).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_schemes.py tests/test_config.py tests/test_material_builder.py`
Expected: PASS (existing material-builder tests included, proving the re-exports).

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/schemes.py src/materialsdb/config.py src/materialsdb/ifc/material_builder.py tests/test_schemes.py tests/test_config.py
git commit -m "feat: named colour palettes module and scheme preferences"
```

---

### Task 2: Style resolution honours scheme and override

**Files:**
- Modify: `src/materialsdb/ifc/material_builder.py` (`style_for`, `MaterialBuilder.__init__`, `_add_style_chain`, `get_surface_style`)
- Modify: `src/materialsdb/gui/listener.py:58` and `:197`
- Modify: `tests/conftest.py` (autouse config isolation)
- Modify: `tests/test_material_builder.py` (append)
- Modify: `tests/test_listener_payload.py` (append)

**Interfaces:**
- Consumes: `schemes.available()`, `config.get_scheme()`, `config.get_ignore_producer_color()`.
- Produces:
  - `style_for(color, category, scheme=None, ignore_producer_color=False) -> tuple[str, tuple[float, float, float]]`
  - `MaterialBuilder(file, country=None, lang=None, style_scheme=None, ignore_producer_color=None)`; instance attrs `style_scheme`, `ignore_producer_color`
  - `MaterialBuilder._add_style_chain(color, category, scheme=None, ignore_producer_color=None)`
  - `MaterialBuilder.get_surface_style(color, category, scheme=None, ignore_producer_color=None)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_material_builder.py`:

```python
def test_style_for_ignore_producer_color_uses_category():
    from materialsdb.ifc.material_builder import CATEGORIES, style_for

    name, rgb = style_for(16711680, "Insulation", ignore_producer_color=True)
    assert name == "category Insulation"
    assert rgb == tuple(c / 255 for c in CATEGORIES["Insulation"]["color"])


def test_style_for_unknown_scheme_falls_back_to_lesosai():
    from materialsdb.ifc.material_builder import style_for

    assert style_for(None, "Concrete", scheme="Nope") == style_for(None, "Concrete")


def test_builder_defaults_style_preferences_from_config(tmp_path, monkeypatch):
    import ifcopenshell

    from materialsdb import schemes
    from materialsdb.ifc.material_builder import MaterialBuilder

    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)
    palette = {k: dict(v) for k, v in schemes.CATEGORIES.items()}
    palette["Concrete"] = {"hatch": "", "color": [1, 2, 3]}
    schemes.save("Mine", palette)
    monkeypatch.setattr("materialsdb.config.get_scheme", lambda: "Mine")
    monkeypatch.setattr("materialsdb.config.get_ignore_producer_color", lambda: True)

    builder = MaterialBuilder(ifcopenshell.file(schema="IFC4"))
    assert builder.style_scheme == "Mine" and builder.ignore_producer_color is True
    name, rgb = builder.get_surface_style(16711680, "Concrete")
    assert name == "category Concrete" and rgb == (1 / 255, 2 / 255, 3 / 255)


def test_builder_explicit_style_arguments_win(tmp_path, monkeypatch):
    import ifcopenshell

    from materialsdb import schemes
    from materialsdb.ifc.material_builder import MaterialBuilder

    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)
    schemes.save("Mine", schemes.CATEGORIES)
    monkeypatch.setattr("materialsdb.config.get_scheme", lambda: "Mine")
    monkeypatch.setattr("materialsdb.config.get_ignore_producer_color", lambda: True)

    builder = MaterialBuilder(ifcopenshell.file(schema="IFC4"), style_scheme="Lesosai", ignore_producer_color=False)
    name, _ = builder.get_surface_style(16711680, "Concrete")
    assert name == "color 16711680"
```

Append to `tests/test_listener_payload.py`:

```python
def test_payload_honours_custom_scheme_and_override(store, tmp_path, monkeypatch):
    """A custom scheme plus the producer-colour override change the resolved style."""
    from materialsdb import schemes

    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)
    palette = {name: dict(style) for name, style in schemes.CATEGORIES.items()}
    palette["Insulation"] = {"hatch": "", "color": [1, 2, 3]}
    schemes.save("Mine", palette)
    monkeypatch.setattr("materialsdb.config.get_scheme", lambda: "Mine")
    monkeypatch.setattr("materialsdb.config.get_ignore_producer_color", lambda: True)

    payload, problems = build_add_materials_payload(store, [{"id": "00000000-0000-0000-0000-000000000001"}])

    assert problems == []
    first = payload["materials"][0]
    assert first["style_name"] == "category Insulation"  # producer colour 0xFF0000 ignored
    assert first["style_color"] == [round(c / 255, 6) for c in (1, 2, 3)]


def test_construction_payload_honours_override(store, monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_ignore_producer_color", lambda: True)
    payload, problems = build_add_construction_payload(
        store,
        body={"name": "w", "layers": [{"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2}]},
    )
    assert problems == []
    assert payload["construction"]["layers"][0]["material"]["style_name"] == "category Insulation"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_material_builder.py tests/test_listener_payload.py`
Expected: the new tests FAIL (`ignore_producer_color` unexpected keyword / styles still show `color …`).

- [ ] **Step 3: Implement**

Add an autouse isolation fixture to `tests/conftest.py` **first** (once builders read config, a developer's saved scheme would otherwise change unrelated test outcomes):

```python
@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)
```

Test-local fixtures that patch the same attribute (e.g. `test_config.py`'s) run later and win, so they keep control of the file they write.

`style_for` body:

```python
def style_for(color, category, scheme=None, ignore_producer_color=False):
    if color and not ignore_producer_color:
        return f"color {int(color)}", _rgb_int_components(int(color))
    palettes = schemes.available()
    palette = palettes.get(scheme or schemes.DEFAULT_SCHEME) or palettes[schemes.DEFAULT_SCHEME]
    category = category if category in palette else "Others"
    return f"category {category}", tuple(component / 255 for component in palette[category]["color"])
```

`MaterialBuilder.__init__` gains the two keyword parameters (documented as the *style* scheme to avoid confusion with `build(scheme=)`, the fingerprint scheme):

```python
self.style_scheme = style_scheme if style_scheme is not None else config.get_scheme()
self.ignore_producer_color = (
    config.get_ignore_producer_color() if ignore_producer_color is None else bool(ignore_producer_color)
)
```

`_add_style_chain` and `get_surface_style` take `scheme=None, ignore_producer_color=None`, resolve `None` to the builder attributes, and pass both to `style_for`. `build()` keeps calling `self._add_style_chain(color, category)` unchanged.

In `gui/listener.py`, both `style_for(color, category)` calls become:

```python
style_name, style_rgb = style_for(
    color,
    category,
    scheme=config.get_scheme(),
    ignore_producer_color=config.get_ignore_producer_color(),
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_material_builder.py tests/test_listener_payload.py tests/test_project_library.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/ifc/material_builder.py src/materialsdb/gui/listener.py tests/conftest.py tests/test_material_builder.py tests/test_listener_payload.py
git commit -m "feat: resolve styles from active scheme and producer-colour preference"
```

---

### Task 3: Server scheme API and extended config

**Files:**
- Modify: `src/materialsdb/gui/server.py` (do_GET dispatch ~742–790, do_POST dispatch ~900–940, `_config` ~1064)
- Modify: `tests/test_gui_server.py` (autouse fixture ~16–25, append tests)

**Interfaces:**
- Consumes: Task 1's `schemes` module + config accessors.
- Produces (used by Tasks 7–9):
  - `GET /api/schemes` → `{"schemes": [{"name", "builtin"}], "error": str | null}`
  - `GET /api/schemes/<name>` → `{"name", "builtin", "categories"}`; `GET /api/schemes/<name>/export` → envelope
  - `POST /api/schemes/<name>` `{"categories"}` → `{"ok": true}`
  - `POST /api/schemes/<name>/rename` `{"new_name"}` → `{"ok": true}`
  - `POST /api/schemes/<name>/delete` → `{"ok": true}`
  - `POST /api/schemes/import` envelope → `{"name": ...}`
  - `GET /api/config` → adds `scheme` (effective), `schemes` (names), `ignore_producer_color`
  - `POST /api/config` accepts `scheme` (must exist, else 400) and `ignore_producer_color`
  - Handler methods on the request class: `_schemes_list`, `_scheme_get(parsed)`, `_scheme_save(name, payload)`, `_scheme_rename(name, payload)`, `_scheme_delete(name)`, `_scheme_import(payload)`

- [ ] **Step 1: Update the autouse fixture and write the failing tests**

In `tests/test_gui_server.py`'s `pinned_fr_ch_config`, add `tmp_path` and redirect the config dir so no real user file leaks in:

```python
def pinned_fr_ch_config(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: config_dir)
    # ...existing lang/country/set_param patches unchanged...
```

Append:

```python
def _custom_palette():
    from materialsdb.ifc.material_builder import CATEGORIES

    return {name: {"hatch": "", "color": list(style["color"])} for name, style in CATEGORIES.items()}


def test_scheme_api_crud_and_active_reset(api):
    server, state = api
    payload = {"categories": _custom_palette()}

    status, _ = request(server, "POST", "/api/schemes/Mon%20Palett%C3%A9", payload=payload, token=state.token)
    assert status == 200
    status, body = request(server, "GET", "/api/schemes")
    assert {"name": "Mon Paletté", "builtin": False} in body["schemes"]
    assert {"name": "Lesosai", "builtin": True} in body["schemes"]

    status, body = request(server, "GET", "/api/schemes/Mon%20Palett%C3%A9")
    assert status == 200 and body["categories"]["Concrete"]["color"] == [0, 255, 0]

    status, _ = request(server, "POST", "/api/config", payload={"scheme": "Mon Paletté"}, token=state.token)
    assert status == 200
    assert request(server, "GET", "/api/config")[1]["scheme"] == "Mon Paletté"

    status, _ = request(server, "POST", "/api/schemes/Mon%20Palett%C3%A9/rename",
                         payload={"new_name": "Palette 2"}, token=state.token)
    assert status == 200
    assert request(server, "GET", "/api/config")[1]["scheme"] == "Palette 2"

    status, _ = request(server, "POST", "/api/schemes/Palette%202/delete", payload={}, token=state.token)
    assert status == 200
    assert request(server, "GET", "/api/config")[1]["scheme"] == "Lesosai"


def test_scheme_api_rejects_builtin_and_invalid(api):
    server, state = api
    assert request(server, "POST", "/api/schemes/Lesosai", payload={"categories": _custom_palette()},
                   token=state.token)[0] == 400
    assert request(server, "POST", "/api/schemes/import", payload={"categories": _custom_palette()},
                   token=state.token)[0] == 400
    assert request(server, "POST", "/api/schemes/Bad", payload={"categories": {"Concrete": {"hatch": "", "color": [0, 0, 0]}}},
                   token=state.token)[0] == 400
    assert request(server, "POST", "/api/config", payload={"scheme": "Nope"}, token=state.token)[0] == 400


def test_scheme_export_import_and_conflict(api):
    server, state = api
    status, envelope = request(server, "GET", "/api/schemes/Lesosai/export")
    assert status == 200 and envelope["format"] == "materialsdb-scheme/1"

    envelope["name"] = "Imported palette"
    status, body = request(server, "POST", "/api/schemes/import", payload=envelope, token=state.token)
    assert status == 200 and body["name"] == "Imported palette"

    status, body = request(server, "POST", "/api/schemes/import", payload=envelope, token=state.token)
    assert status == 409


def test_scheme_api_surfaces_corrupt_file(api, tmp_path):
    server, _ = api
    (tmp_path / "config" / "schemes.json").write_text("{not json")
    status, body = request(server, "GET", "/api/schemes")
    assert status == 200
    assert [item["name"] for item in body["schemes"]] == ["Lesosai"]
    assert body["error"]


def test_config_scheme_and_override_roundtrip(api):
    server, state = api
    status, _ = request(server, "POST", "/api/config",
                        payload={"ignore_producer_color": True}, token=state.token)
    assert status == 200
    body = request(server, "GET", "/api/config")[1]
    assert body["ignore_producer_color"] is True
    assert body["scheme"] == "Lesosai"
    assert "Lesosai" in body["schemes"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_gui_server.py -k "scheme or config_scheme"`
Expected: FAIL with 404/`KeyError` (endpoints absent).

- [ ] **Step 3: Implement the GET/POST branches and handlers**

GET, after the `/api/constructions` block:

```python
if parsed.path == "/api/schemes":
    names = list(schemes.available())
    self._send(200, {"schemes": [{"name": n, "builtin": n in schemes.BUILTIN} for n in names],
                     "error": schemes.load_error()})
    return
if parsed.path.startswith("/api/schemes/"):
    self._scheme_get(parsed)
    return
```

`_scheme_get(parsed)`: `rest = unquote(parsed.path[len("/api/schemes/"):])`; `export = rest.endswith("/export")`; `name = rest[:-len("/export")] if export else rest`; `palette = schemes.get(name)` → 404 if None; export → `schemes.export_payload(name)`, else `{"name", "builtin", "categories"}`.

POST, before the `/api/constructions/` catch-all (order matters: exact `import` first):

```python
elif parsed.path == "/api/schemes/import":
    self._scheme_import(payload)
elif parsed.path.startswith("/api/schemes/"):
    self._scheme_post(parsed, payload)
```

`_scheme_post`: `rest = unquote(parsed.path[len("/api/schemes/"):])`; if it ends `/rename` or `/delete` → name + action; else the whole rest is the name → create/overwrite. `_scheme_save` rejects `name in schemes.BUILTIN` (400) then `schemes.save`. `_scheme_rename` catches `SchemeError` → 400. `_scheme_delete`: 404 when `schemes.get(name)` is None, 400 when builtin, else `schemes.delete` + reset `config.set_scheme(schemes.DEFAULT_SCHEME)` when the deleted name was active. Rename follows the active name by calling `config.set_scheme(new)` when the old name was active. All `SchemeError`s → `400 {"error": str(err)}`; import name collision → `409`.

`_scheme_import(payload)`: if `isinstance(payload, dict)` and `payload.get("name")` is an existing name in `schemes.available()` → 409; else `schemes.import_payload(payload)` → 200 `{"name": ...}`; `SchemeError` → 400.

Extend `_config`: when `"scheme"` in payload, resolve `scheme = str(payload["scheme"])`; `scheme in schemes.available()` else 400; `config.set_scheme(scheme)`. When `"ignore_producer_color"` in payload → `config.set_ignore_producer_color(bool(payload["ignore_producer_color"]))`.

Extend the GET `/api/config` body to:

```python
{
    "lang": cfg.get_lang(),
    "country": cfg.get_country(),
    "scheme": cfg.get_scheme() or schemes.DEFAULT_SCHEME,
    "schemes": list(schemes.available()),
    "ignore_producer_color": cfg.get_ignore_producer_color(),
}
```

(Import `schemes` at module level next to `config`; keep `from materialsdb import config` usage as-is.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_gui_server.py`
Expected: PASS (existing config tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/server.py tests/test_gui_server.py
git commit -m "feat(gui): scheme CRUD/export/import API and config extension"
```

---

### Task 4: Server restyle endpoint

**Files:**
- Modify: `src/materialsdb/gui/server.py` (add `_restyle_plan` near `_force_plan`, `_model_restyle` near `_model_force_update`, POST dispatch)
- Modify: `tests/test_gui_server.py` (append)

**Interfaces:**
- Consumes: `store_.get_model_materials`, `store_.get`, `style_for`, `config.get_scheme`, `config.get_ignore_producer_color`, `_active_model_path`.
- Produces: `_restyle_plan(store_, model_path, scheme, ignore_producer_color) -> tuple[list[dict], list[str]]` where entries are `{"material_id", "style_name", "style_color"}` and the second list is skipped ids; `POST /api/model/restyle` → `{"queued": n, "skipped": [...], "scheme": name}` and queues `{"action": "restyle", "materials": entries}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_server.py` (reuse `_ingest_model` from the force-update tests):

```python
def test_model_restyle_queues_all_store_materials_and_reports_skipped(api):
    server, state = api
    store_ = state.resolve_store()
    path = "/m/restyle.ifc"
    _ingest_model(
        server,
        state,
        path,
        {
            M1: {"fingerprint": None, "scheme": None, "used_in": ["IfcWall"], "layers": []},
            M2: {"fingerprint": None, "scheme": None, "used_in": [], "layers": []},  # unused still restyled
            "ghost": {"fingerprint": None, "scheme": None, "used_in": [], "layers": []},
        },
    )

    status, body = request(server, "POST", "/api/model/restyle", payload={}, token=state.token)

    assert status == 200
    assert body["queued"] == 2 and body["skipped"] == ["ghost"]
    assert body["scheme"] == "Lesosai"
    pending = state.listeners["c1"]["pending"]
    assert pending["action"] == "restyle"
    assert {entry["material_id"] for entry in pending["materials"]} == {M1, M2}
    assert all(set(entry) == {"material_id", "style_name", "style_color"} for entry in pending["materials"])


def test_model_restyle_uses_active_scheme_and_override(api, monkeypatch):
    server, state = api
    _ingest_model(server, state, "/m/restyle2.ifc", {M1: {"fingerprint": None, "scheme": None, "used_in": [], "layers": []}})
    calls = []

    def spy(color, category, scheme=None, ignore_producer_color=False):
        calls.append((scheme, ignore_producer_color))
        return "category Insulation", (1.0, 0.0, 0.0)

    monkeypatch.setattr("materialsdb.gui.listener.style_for", spy)
    monkeypatch.setattr("materialsdb.config.get_scheme", lambda: "Mine")
    monkeypatch.setattr("materialsdb.config.get_ignore_producer_color", lambda: True)

    status, body = request(server, "POST", "/api/model/restyle", payload={}, token=state.token)

    assert status == 200
    assert calls == [("Mine", True)]
    entry = state.listeners["c1"]["pending"]["materials"][0]
    assert entry["style_name"] == "category Insulation" and entry["style_color"] == [1.0, 0.0, 0.0]


def test_model_restyle_requires_listener(api):
    server, state = api
    status, body = request(server, "POST", "/api/model/restyle", payload={}, token=state.token)
    assert status == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_gui_server.py -k restyle`
Expected: FAIL with 404.

- [ ] **Step 3: Implement**

```python
def _restyle_plan(store_, model_path, scheme, ignore_producer_color):
    """(entries, skipped): style-only entries for every model material still in the store."""
    from materialsdb.gui.listener import style_for

    entries, skipped = [], []
    for material_id in store_.get_model_materials(model_path):
        material = store_.get(material_id)
        if material is None:
            skipped.append(material_id)
            continue
        style_name, rgb = style_for(
            getattr(material.information, "color", None),
            str(getattr(material.information, "group", "") or ""),
            scheme=scheme,
            ignore_producer_color=ignore_producer_color,
        )
        entries.append({"material_id": material_id, "style_name": style_name,
                        "style_color": [round(component, 6) for component in rgb]})
    return entries, skipped
```

`_model_restyle` mirrors `_model_force_update`'s model/ listener selection and 409 messages, then:

```python
scheme = config.get_scheme() or schemes.DEFAULT_SCHEME
entries, skipped = _restyle_plan(store_, model_path, scheme, config.get_ignore_producer_color())
if entries:
    listener = self.state.listeners[client_id]
    listener["pending"] = {"action": "restyle", "materials": entries}
    listener["last_status"] = {"status": "pending", "detail": ""}
self._send(200, {"queued": len(entries), "skipped": skipped, "scheme": scheme})
```

Add to the POST dispatch next to force-update: `elif parsed.path == "/api/model/restyle": self._model_restyle(store_, payload)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_gui_server.py -k "restyle or force"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/server.py tests/test_gui_server.py
git commit -m "feat(gui): style-only restyle plan and endpoint"
```

---

### Task 5: Add-on restyle action and recolour-on-reuse

**Files:**
- Modify: `bonsai_addon/insert.py` (`_apply_style` ~434–470; add `_all_materials_by_id`, `apply_restyle` near `existing_materials_by_id`)
- Modify: `bonsai_addon/__init__.py` (dispatch ~52–72, `_undo_label` ~107)
- Modify: `tests/test_bonsai_insert.py` (append)
- Modify: `bonsai_addon/test/test_in_blender.py` (append a restyle test)
- Rebuild: `dist/materialsdb_listener-0.3.3.zip`

**Interfaces:**
- Consumes: existing `_apply_style`, `_should_style`, `_material_id_of`, `_ORIGINAL_STYLE_NAME` regex; `_sync_style_materials` in `__init__.py`.
- Produces: `insert.apply_restyle(file, payload, summary=None) -> int` with `summary["kept_styles"]: list[str]`, `summary["missing"]: list[str]`; payload `{"action": "restyle", "materials": [{"material_id", "style_name", "style_color"}]}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bonsai_insert.py` (reuse `store`, `_payload`, `_file_with_body_context` helpers):

```python
def _style_colour(file, name):
    style = next(s for s in file.by_type("IfcSurfaceStyle") if s.Name == name)
    colour = next(s for s in style.Styles if s.is_a("IfcSurfaceStyleShading")).SurfaceColour
    return (round(colour.Red * 255), round(colour.Green * 255), round(colour.Blue * 255))


def test_apply_style_updates_reused_style_colour(store):
    file = _file_with_body_context()
    payload = _payload(store)
    apply_add_materials(file, payload)  # creates the style
    name = payload["materials"][0]["style_name"]

    changed = copy.deepcopy(payload)
    for entry in changed["materials"]:
        entry["style_color"] = [0.0, 1.0, 0.0]
    apply_add_materials(file, changed)

    assert _style_colour(file, name) == (0, 255, 0)


def test_apply_style_same_colour_leaves_style_values_untouched(store):
    file = _file_with_body_context()
    payload = _payload(store)
    apply_add_materials(file, payload)
    name = payload["materials"][0]["style_name"]
    before = _style_colour(file, name)

    apply_add_materials(file, copy.deepcopy(payload))

    assert _style_colour(file, name) == before  # idempotent, no false write


def test_apply_restyle_refreshes_ours_keeps_foreign_and_reports_missing(store):
    file = _file_with_body_context()
    payload = _payload(store)
    apply_add_materials(file, payload)
    material_id = payload["materials"][0]["identity"]["material_id"]

    # give the first entity a third-party style, as test_apply_keeps_third_party_style does
    foreign_material = file.by_type("IfcMaterial")[0]
    custom = file.create_entity("IfcSurfaceStyle", Name="MyCustom", Side="BOTH")
    ifcopenshell.api.run(
        "style.add_surface_style",
        file,
        style=custom,
        ifc_class="IfcSurfaceStyleShading",
        attributes={"SurfaceColour": {"Name": None, "Red": 0.1, "Green": 0.2, "Blue": 0.3}},
    )
    styled_item = next(
        item
        for representation in foreign_material.HasRepresentation
        for styled in representation.Representations
        for item in styled.Items
        if item.is_a("IfcStyledItem")
    )
    styled_item.Styles = (custom,)

    summary = {}
    count = apply_restyle(
        file,
        {"action": "restyle", "materials": [
            {"material_id": material_id, "style_name": "category Insulation", "style_color": [0.2, 0.4, 0.6]},
            {"material_id": "does-not-exist", "style_name": "category Others", "style_color": [1.0, 1.0, 1.0]},
        ]},
        summary=summary,
    )

    assert styled_item.Styles[0].Name == "MyCustom"  # foreign style untouched
    assert summary["kept_styles"] == [material_id]
    assert summary["missing"] == ["does-not-exist"]
    assert count == len(payload["materials"]) - 1  # the other same-id entity was restyled


def test_apply_restyle_styles_all_entities_sharing_material_id(store):
    file = _file_with_body_context()
    payload = _payload(store)  # 2-layer material -> two IfcMaterial with one id
    apply_add_materials(file, payload)
    material_id = payload["materials"][0]["identity"]["material_id"]

    apply_restyle(file, {"action": "restyle", "materials": [
        {"material_id": material_id, "style_name": "category Insulation", "style_color": [0.2, 0.4, 0.6]},
    ]})

    styled = [m for m in file.by_type("IfcMaterial") if ifcopenshell.util.element.get_psets(m).get("materialsdb", {}).get("material_id") == material_id]
    assert styled and all(any(item.is_a("IfcStyledItem") for rep in m.HasRepresentation for styled_rep in rep.Representations for item in styled_rep.Items) for m in styled)
```

(Adjust helper names to the ones the file already uses; the foreign-style-kept assertion: give one of the materials a `MyCustom` style first, like `test_apply_keeps_third_party_style`, and assert `summary["kept_styles"]` contains its id and its style name survived.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_bonsai_insert.py -k "restyle or reused or untouched"`
Expected: FAIL (`apply_restyle` undefined; reused style keeps the old colour).

- [ ] **Step 3: Implement**

`_apply_style` reuse branch: when `style` came from the existing by-name map, locate its `IfcSurfaceStyleShading.SurfaceColour`; compare components as 0–255 ints (`round(value * 255)`) with the requested rgb; when any differ, assign `Red/Green/Blue = rgb[0..2]`. Never rewrite when equal. When the reused style has no `IfcSurfaceStyleShading` with a `SurfaceColour`, leave it as-is — unknown structures are never mutated.

`apply_restyle`:

```python
def apply_restyle(file, payload, summary=None) -> int:
    """Style-only refresh: never touches psets, identity, layers or thicknesses."""
    if summary is None:
        summary = {}
    summary.setdefault("kept_styles", [])
    summary.setdefault("missing", [])
    by_id = {}
    for material in file.by_type("IfcMaterial"):
        material_id = _material_id_of(material)
        if material_id is not None:
            by_id.setdefault(material_id, []).append(material)
    restyled = 0
    for entry in payload.get("materials") or []:
        material_id = str(entry.get("material_id") or "")
        targets = by_id.get(material_id) or []
        if not targets:
            summary["missing"].append(material_id)
            continue
        for material in targets:
            if not _should_style(material):
                summary["kept_styles"].append(material_id)
                continue
            _apply_style(file, entry, material)
            restyled += 1
    return restyled
```

`__init__.py` dispatch:

```python
elif action == "restyle":
    summary = {}
    count = insert.apply_restyle(tool.Ifc.get(), payload, summary=summary)
    _sync_style_materials(tool.Ifc.get())
    detail = f"{count} material(s) restyled"
    kept, missing = sorted(set(summary["kept_styles"])), sorted(set(summary["missing"]))
    if kept:
        detail += f", {len(kept)} kept (foreign style)"
    if missing:
        detail += f", {len(missing)} not in model"
    _CLIENT.report("applied", detail)
```

`_undo_label`: `if action == "restyle": return f"materialsdb: restyle {len(payload.get('materials') or [])} material(s)"`.

`_sync_style_materials` must refresh existing linked Blender materials, not only create missing ones — it already calls `tool.Style.switch_shading`; the in-Blender test below verifies the base colour actually changes. If it does not, extend the function to assign the Principled BSDF Base Color from the IFC surface colour after `switch_shading`.

In `bonsai_addon/test/test_in_blender.py`, add a restyle test following the existing style test: push a materials payload, record the linked Blender material's Principled BSDF `Base Color`, apply a restyle payload with a different colour, assert the default_value changed and that the `IfcSurfaceStyle`'s `SurfaceColour` matches.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_bonsai_insert.py`
Expected: PASS.
Run (local-only, needs Blender): `python3 -m pytest bonsai_addon/test -q`
Expected: PASS including the new restyle test; if the viewport colour did not refresh, fix `_sync_style_materials` and rerun.

- [ ] **Step 5: Rebuild the add-on zip and commit**

```bash
python3 dev_utils/build_bonsai_addon.py --version 0.3.3
git add bonsai_addon/insert.py bonsai_addon/__init__.py bonsai_addon/test/test_in_blender.py tests/test_bonsai_insert.py
git commit -m "feat(bonsai): style-only restyle action and recolour-on-reuse"
```

---

### Task 6: Settings page shell, static routes, nav cleanup

**Files:**
- Create: `src/materialsdb/gui/static/settings.html`
- Create: `src/materialsdb/gui/static/settings.js` (minimal placeholder — Task 7 replaces its contents)
- Modify: `src/materialsdb/gui/server.py` (do_GET static routes ~717–741)
- Modify: `src/materialsdb/gui/static/index.html` (nav line 38, panel lines 39–42)
- Modify: `src/materialsdb/gui/static/constructions.html` (nav line 32, panel lines 33–36)
- Modify: `src/materialsdb/gui/static/app.js` (lines 16–22, 118–121, 758–772, 780–784)
- Modify: `src/materialsdb/gui/static/app-constructions.js` (initConfig ~253–261, handlers 561–565 and 642–648)
- Modify: `tests/test_gui_server.py` (append)

**Interfaces:**
- Produces the element IDs Tasks 7–9 rely on:
  `lang`, `country`, `scheme`, `ignore-producer-color`, `apply-colours`, `apply-status`, `scheme-list`, `scheme-name`, `clone-scheme`, `rename-scheme`, `delete-scheme`, `export-scheme`, `import-scheme`, `import-file`, `import-name`, `palette-rows`, `preview-chips`, `preview-section`, `save-scheme`, `save-as-scheme`, `editor-status`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_server.py`:

```python
def test_settings_page_and_script_are_served(api):
    server, state = api
    status, body = request(server, "GET", "/settings.html")
    assert status == 200 and state.token.encode() in body and b"__TOKEN__" not in body
    status, body, headers = request(server, "GET", "/settings.js", want_headers=True)
    assert status == 200
    assert headers["Content-Type"].startswith("text/javascript")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_gui_server.py::test_settings_page_and_script_are_served`
Expected: FAIL (404).

- [ ] **Step 3: Implement the page, routes and nav cleanup**

Create `settings.html` mirroring `index.html`'s head/style/script layout (same nav CSS, token script `window.MATERIALSDB_TOKEN = "__TOKEN__"`, `<script src="/settings.js">`), with nav `Materials | Constructions | Settings` (Settings active) and three sections: **General** (language/country selects), **Colour scheme** (scheme select, override checkbox, apply button + status), **Palettes** (list, name field, clone/rename/delete/export/import controls, `palette-rows`, `preview-chips`, `preview-section`, save/save-as, status). Give every control the exact ID from the Interfaces block; use `style="display:none"`/`disabled` states as needed.

Also create a minimal `settings.js` placeholder (header comment plus `const TOKEN = window.MATERIALSDB_TOKEN;` and an `api()` helper matching `app-constructions.js`) so the static route test below is meaningful; Task 7 replaces it with the real logic.

In `server.py`'s do_GET, add branches mirroring `/constructions.html` and `/app-constructions.js` for `/settings.html` (replace `__TOKEN__`) and `/settings.js` (`text/javascript`).

`index.html` / `constructions.html`: nav link becomes `<a href="/settings.html">Settings</a>`; delete the `#settings-panel` div.

`app.js`: delete the `settings-panel` removal in the embed block (keep `.top-tabs` removal), delete the `#lang`/`#country` element assignment in `loadMaterials` (keep `lang = cfg.lang || lang` and `document.documentElement.lang = lang`), delete the `#lang`/`#country` change handlers, and delete the `settings-tab` click handler.

`app-constructions.js`: same removal in `initConfig` (keep fetching `/api/config` and setting `document.documentElement.lang`), delete the settings-tab handler and the `#lang`/`#country` handlers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_gui_server.py tests/test_construction_frontend.py tests/test_model_changes_frontend.py tests/test_picker_frontend.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/static/settings.html src/materialsdb/gui/server.py src/materialsdb/gui/static/index.html src/materialsdb/gui/static/constructions.html src/materialsdb/gui/static/app.js src/materialsdb/gui/static/app-constructions.js tests/test_gui_server.py
git commit -m "feat(gui): standalone settings page shell and nav cleanup"
```

---

### Task 7: Settings page — general, colour scheme, apply colours

**Files:**
- Create: `src/materialsdb/gui/static/settings.js`
- Create: `tests/harness/settings_page_harness.mjs`
- Create: `tests/test_settings_frontend.py`

**Interfaces:**
- Consumes: the Task 6 IDs; `GET /api/config`, `POST /api/config`, `GET /api/schemes`, `GET /api/model/changes`, `POST /api/model/restyle`.
- Produces (Task 8–9 extend the same file):
  - `loadSettings()` — config + schemes + model state, then `populateSchemeSelect()`, `renderSchemeList()`
  - `saveConfig(patch)` — `POST /api/config` with the token header
  - `applyColours()` — `POST /api/model/restyle`, writes `#apply-status`, then `refreshApplyStatus()`
  - `refreshApplyStatus()` — `GET /api/listener/clients`, appends the active model client's `last_status.detail`
  - `api(path, options)` helper copied from `app-constructions.js` (token header)

- [ ] **Step 1: Write the failing harness test**

`tests/harness/settings_page_harness.mjs`: copy the DOM stub from `model_changes_harness.mjs` (including `checked`/`disabled`, `classList`, `dispatch`), then stub fetch:

```js
const config = { lang: "fr", country: "CH", scheme: "Lesosai", schemes: ["Lesosai", "Mine"], ignore_producer_color: false };
let restyleCalls = 0;
let posts = [];
async function fakeFetch(path, options = {}) {
  const json = (data) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  if (path === "/api/config" && (!options.method || options.method === "GET")) return json(config);
  if (path === "/api/config") { posts.push(JSON.parse(options.body)); config.ignore_producer_color = JSON.parse(options.body).ignore_producer_color ?? config.ignore_producer_color; return json({ ok: true }); }
  if (path === "/api/schemes") return json({ schemes: [{ name: "Lesosai", builtin: true }, { name: "Mine", builtin: false }], error: null });
  if (path === "/api/model/changes") return json({ model_path: "/m/x.ifc", counts: {} });
  if (path === "/api/model/restyle") { restyleCalls += 1; return json({ queued: 3, skipped: [], scheme: "Lesosai" }); }
  if (path === "/api/listener/clients") return json({ clients: [{ model_path: "/m/x.ifc", last_status: { status: "applied", detail: "3 material(s) restyled" } }] });
  throw new Error("unhandled fetch: " + path);
}
```

Load `settings.js` in a VM like the other harnesses, then assert and print:

- `#scheme` options include `Lesosai` and `Mine`, value equals `config.scheme`;
- dispatching `change` on `#ignore-producer-color` after setting `checked = true` posts `{ignore_producer_color: true}`;
- dispatching `change` on `#scheme` (value `Mine`) posts `{scheme: "Mine"}`;
- `#apply-colours` is enabled when `/api/model/changes` has a `model_path`, and clicking it calls
  `/api/model/restyle` and writes `3` plus the listener's `3 material(s) restyled` detail into `#apply-status`.

`tests/test_settings_frontend.py` runs it exactly like `test_construction_frontend.py` (node skip guard, subprocess, assert stdout markers such as `scheme options: OK`, `override post: OK`, `apply: OK`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_settings_frontend.py`
Expected: FAIL (harness file missing / settings.js missing).

- [ ] **Step 3: Implement `settings.js` (general + colour scheme)**

Copy the `api()` helper shape from `app-constructions.js`. `loadSettings()` fetches config, schemes and model changes in parallel; sets `#lang`/`#country`/`#scheme`/`#ignore-producer-color`; disables `#apply-colours` with a hint when `model_path` is falsy; on change posts the matching patch. When the schemes payload carries a non-null `error` (corrupt `schemes.json`), show it in `#editor-status`. `applyColours()` posts `{}` to `/api/model/restyle`, writes `"$queued queued, $skipped not in store"` into `#apply-status`, then calls `refreshApplyStatus()` which appends the matching listener client's `last_status.detail` when the status is `applied`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_settings_frontend.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/static/settings.js tests/harness/settings_page_harness.mjs tests/test_settings_frontend.py
git commit -m "feat(gui): settings general and colour scheme controls"
```

---

### Task 8: Palette editor core (clone, edit, preview, save)

**Files:**
- Modify: `src/materialsdb/gui/static/settings.js`
- Modify: `tests/harness/settings_page_harness.mjs`
- Modify: `tests/test_settings_frontend.py`

**Interfaces:**
- Produces: `renderSchemeList()`, `selectScheme(name)`, `cloneSelected()`, `renderEditor()`, `renderPreview()`, `saveDraft(asNewName?)`; module state `draft` (`{name, categories, builtin, dirty}`).
- Editor rows are rendered with innerHTML: `<input type="color" data-category="<name>" value="#rrggbb">` + `<input type="text" data-role="hex" data-category="...">`; a delegated `input` listener on `#palette-rows` reads `event.target.dataset` and updates `draft` then `renderPreview()`.

- [ ] **Step 1: Add the failing harness assertions**

Extend the harness: `/api/schemes/Lesosai` returns the full 17-category palette (reuse a small literal with 2–3 categories is not enough — generate all 17 hex values in the harness). Assert:

- clicking `#clone-scheme` fills `#palette-rows` with 17 `data-category` rows and enables `#save-as-scheme`;
- dispatching an `input` event on `#palette-rows` with `{target: {dataset: {category: "Concrete"}, value: "#112233"}}` updates `#preview-chips` HTML to contain `#112233`;
- clicking `#save-as-scheme` with `#scheme-name` set posts to `/api/schemes/<encoded name>` a body whose `categories.Concrete.color` is `[17, 34, 51]`.

Print `editor rows: OK`, `preview: OK`, `save payload: OK`; assert them in `tests/test_settings_frontend.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_settings_frontend.py`
Expected: FAIL on the new markers.

- [ ] **Step 3: Implement the editor core**

- `renderSchemeList()`: one entry per `schemes` item, built-ins marked read-only; clicking selects it.
- `cloneSelected()`: deep-copy the selected palette into `draft`, `dirty = true`, `renderEditor()`, `renderPreview()`.
- `renderEditor()`: rows for all 17 categories from `draft`, colour input value `#rrggbb` from the draft colour, hex text input synced; delegated input events update `draft` (parse `#rrggbb`), re-render the chip and `#preview-section`.
- `renderPreview()`: chips (`#preview-chips`) plus `#preview-section` built from a fixed sample list of categories (`Render`, `Masonry`, `Insulation`, `Wood_Timberproducts`, `Concrete`) as stacked `div`s with the draft colours.
- `saveDraft(asNewName?)`: `POST /api/schemes/<encodeURIComponent(name)>` with `{categories: draft.categories}`; clear `dirty`, refresh the scheme list/config.
- `beforeunload` guard when `dirty`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_settings_frontend.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/static/settings.js tests/harness/settings_page_harness.mjs tests/test_settings_frontend.py
git commit -m "feat(gui): palette editor core with live preview"
```

---

### Task 9: Palette rename, delete, export, import

**Files:**
- Modify: `src/materialsdb/gui/static/settings.js`
- Modify: `tests/harness/settings_page_harness.mjs`
- Modify: `tests/test_settings_frontend.py`

**Interfaces:**
- Produces: `renameSelected()`, `deleteSelected()`, `exportSelected()`, `importPalette(file)`.
- Export is an anchor rendered into `#export-scheme` with `href="/api/schemes/<encoded>/export"` and `download="<name>.json"` (no token needed on GET).
- Import reads `file.text()`, parses the envelope, posts it to `/api/schemes/import`; on 409 shows the conflict in `#editor-status` and keeps `#import-name` editable.

- [ ] **Step 1: Add the failing harness assertions**

Extend the harness with `/api/schemes/Mine/rename`, `/api/schemes/Mine/delete`, and `/api/schemes/import` (`409` when the name is "Lesosai", `200` otherwise). Assert:

- rename posts `{new_name: "Better"}`;
- delete posts to `/api/schemes/Mine/delete`;
- `#export-scheme` contains an anchor with `href` `/api/schemes/Mine/export`;
- importing shows a conflict: set `#import-file.files = [{name: "p.json", text: async () => JSON.stringify(envelope)}]` with `envelope.name = "Lesosai"`, dispatch `change` on `#import-file`, and assert `#editor-status` contains a conflict message while the request to `/api/schemes/import` returned `409`.

Print `rename: OK`, `delete: OK`, `export: OK`, `import conflict: OK`; assert them in the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_settings_frontend.py`
Expected: FAIL on the new markers.

- [ ] **Step 3: Implement**

Follow Task 3's endpoint shapes; encode names with `encodeURIComponent`. Import uses `file.text()`; validate the envelope format client-side before posting, surface `409` text in `#editor-status`, and select the imported scheme on success.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender -q tests/test_settings_frontend.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/static/settings.js tests/harness/settings_page_harness.mjs tests/test_settings_frontend.py
git commit -m "feat(gui): palette rename, delete, export and import"
```

---

### Task 10: Release-candidate verification

**Files:** none (verification only; fix any failure in the owning task and amend its commit).

- [ ] **Step 1: Full suite and linters**

Run:

```bash
python3 -m pytest -p no:pytest-blender -q
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon
ruff format --check .
ty check --project .
```

Expected: all tests pass; ruff and ty clean.

- [ ] **Step 2: Rebuild the add-on zip**

Run: `python3 dev_utils/build_bonsai_addon.py --version 0.3.3`
Expected: `dist/materialsdb_listener-0.3.3.zip` written.

- [ ] **Step 3: Manual smoke (user's machine)**

1. Start the GUI server from the repo (`PYTHONPATH=src python3 -m materialsdb.gui`) and install the rebuilt zip in Blender.
2. Open Settings: confirm the scheme list shows Lesosai, clone it, change `Insulation`, save as a new name, select it, and click **Apply colours to model** with the model open — insulation layers should repaint in the viewport.
3. Foreign-styled materials must keep their style; the status must report kept/skipped counts.
4. Re-run **force update used materials** on the Materials page to confirm it still works and now also repaints colour changes.

- [ ] **Step 4: Report and wait**

Summarise results for the user; do not push or release without their explicit go-ahead (all gates must be green first).
