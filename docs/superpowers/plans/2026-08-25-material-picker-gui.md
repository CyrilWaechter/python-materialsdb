# Web-UI Material Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stdlib-only local web application (`materialsdb-gui`) for exploring cached materials with instant sort/filter/search, multi-select export to standalone `.ifc`, and append-into-existing-file sessions - doubling as the future host-plugin HTTP API.

**Architecture:** New `src/materialsdb/gui/` package: `server.py` (ThreadingHTTPServer + router, loopback bind, per-launch CSRF token), `__main__.py` entry point, `static/` vanilla JS frontend. Core stays GUI-free (one-way imports). Small sanctioned core extension first: type-aware `MaterialSummary` (+`mtype` store column with automatic schema-v2 rebuild) so the 106 btk and 43 construction materials display honestly instead of as mystery blanks.

**Tech Stack:** Python stdlib only (http.server, json, secrets, threading, webbrowser), existing store/query/material_builder APIs, pytest + http.client for endpoint tests.

## Global Constraints

- Zero new runtime dependencies; `[project.scripts] materialsdb-gui = "materialsdb.gui.__main__:main"`; package-data patterns gain `*.html` and `*.js`.
- Server binds `127.0.0.1` ONLY. Every mutating endpoint requires header `X-MaterialsDB-Token` matching `state.token`; mismatch -> 403 before any other logic.
- Core stays GUI-free: `materialsdb.gui` may import query/store/config/utils/material_builder; nothing outside `materialsdb.gui` may import `materialsdb.gui`.
- Store schema: add `mtype TEXT` column, `SCHEMA_VERSION = "2"`; the existing version-mismatch wipe-and-rebuild is the migration (transparent). INSERT/SELECT stay explicitly column-named.
- `MaterialSummary.type` semantics: `simple` -> layer aggregation (unchanged); `btk` -> thickness from `variations.vgeometry`, lambda stays None; `construction` -> all metrics None.
- Status codes: unknown id -> 404; invalid input/path -> 400; session-required action without one -> 409; token mismatch -> 403. Errors are JSON `{"error": "..."}`.
- Tooling gates green after every task: suite via `python3 -m pytest -p no:pytest-blender -q`; ruff check/format and ty per AGENTS.md commands (local untracked scratch files excluded as usual).
- Frontend JS/HTML is untested by automation (spec limitation); the HTTP layer IS fully tested.
- Do NOT touch `tests/fixtures/mini_producer.xml` (golden characterization pins depend on it) - new types get their own fixture file.
- Commit style: short lowercase imperative.

## Key current signatures (orientation)

```python
MaterialSummary(id, company_id, company, category, names, descriptions,
                lambda_min, lambda_max, thick_min, thick_max, usage)
summarize_material(material, company_id="", company="", country=None)
store.summaries(company=None, category=None, min_lambda=None, max_lambda=None,
                min_thick=None, max_thick=None, usage=None, text=None,
                sort="company", ascending=True, lang=None)
store.get(id); store.get_summary(id); query.get_store() (lru_cached);
query.refresh(force=False) -> Report(existing, updated, deleted, skipped)
material_builder.add_material(file, material, company_id="", company="", verxml=None, replace=False)
material_builder.create_material_file(material_id, schema="IFC4", store_=None)
ProjectLibrary(schema="IFC4"); .create_project_library(company, companyid, ver, crd, role="MANUFACTURER")
utils.new_tdatetime(); utils.get_by_country(values, country); config.set_lang/set_country
store internals: _MATERIAL_COLUMNS tuple ('xml' last), _COLUMN_LIST join, SCHEMA_VERSION="1",
  INSERT INTO materials ({_COLUMN_LIST}) VALUES (?, x13) positional in _upsert_file
classes: variation.vgeometry List[Vgeometry](country, thick, density);
         variation.vthermal List[Vthermal](country, U_value_without, ...);
         material.construction attrs consref/designusage + text body
```

---

### Task 1: Type-aware summaries + store mtype column (schema v2)

**Files:**
- Modify: `src/materialsdb/summary.py`, `src/materialsdb/store.py`
- Create: `tests/fixtures/mixed_types.xml`
- Modify: `tests/conftest.py`, `tests/test_summary.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: shapes above; `classes.Variation.vgeometry`
- Produces (all later tasks depend on these):

```python
MaterialSummary(..., type: str)      # field AFTER category
store.SCHEMA_VERSION == "2"
store.summaries(..., type=None, ...) # NEW keyword filter -> WHERE mtype=?
conftest fixtures mixed_xml / mixed_source
```

- [ ] **Step 1: Create mixed-types fixture + conftest fixtures**

Create `tests/fixtures/mixed_types.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<materials company="Mixed SA" companyid="B2C85A67-5B1E-4960-A297-2DE8275049C6" ver="1" crd="43979.7189866898" verXML="3" xmlns="http://www.materialsdb.org">
  <material id="00000000-0000-0000-0000-000000000004" readonly="1" type="btk">
    <information group="Insulation">
      <names>
        <name lang="fr">Complexe D</name>
      </names>
    </information>
    <variations>
      <variation id="00000000-0000-0000-0000-0000000000d1">
        <vgeometry country="CH" thick="200"/>
        <vthermal country="CH" U_value_without="0.25"/>
      </variation>
      <variation id="00000000-0000-0000-0000-0000000000d2">
        <vgeometry country="CH" thick="300"/>
        <vthermal country="CH" U_value_without="0.18"/>
      </variation>
    </variations>
  </material>
  <material id="00000000-0000-0000-0000-000000000005" readonly="1" type="construction">
    <information group="Others">
      <names>
        <name lang="fr">Mur E</name>
      </names>
    </information>
    <construction consref="REF-E" designusage="consDesignForWall">001[0.2@548268BE-BAC3-4E54-808D-A9524C558D13]</construction>
  </material>
</materials>
```

Append to `tests/conftest.py` after existing fixtures:

```python
@pytest.fixture
def mixed_xml() -> Path:
    return FIXTURES / "mixed_types.xml"


@pytest.fixture
def mixed_source(mixed_xml):
    return XmlDeserialiser().from_xml(str(mixed_xml))
```

- [ ] **Step 2: Write failing summary tests**

Append to `tests/test_summary.py`:

```python
def test_btk_summary_thickness_from_variations(mixed_source):
    from materialsdb.summary import summarize_material

    s = summarize_material(mixed_source.material[0], country="CH")

    assert s.type == "btk"
    assert s.thick_min == 200
    assert s.thick_max == 300
    assert s.lambda_min is None


def test_construction_summary_has_no_metrics(mixed_source):
    from materialsdb.summary import summarize_material

    s = summarize_material(mixed_source.material[1])

    assert s.type == "construction"
    assert s.lambda_min is None
    assert s.thick_max is None


def test_simple_summary_keeps_type_simple(mini_source):
    from materialsdb.summary import summarize_material

    assert summarize_material(mini_source.material[0]).type == "simple"
```

Run: `python3 -m pytest -p no:pytest-blender tests/test_summary.py -v`
Expected: FAIL (TypeError/AttributeError on missing `type`).

- [ ] **Step 3: Implement summary changes**

In `summary.py`: add `    type: str` directly after the `category: str` field; then replace everything from `lambdas = []` through the end of `summarize_material` with:

```python
    mtype = str(getattr(material, "type", "") or "simple")

    lambdas = []
    thicks = []
    if mtype == "btk":
        variations = getattr(getattr(material, "variations", None), "variation", ()) or ()
        for variation in variations:
            geometry = utils.get_by_country(variation.vgeometry or (), country)
            if geometry is not None:
                thicks.append(geometry.thick)
    else:
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
        type=mtype,
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

Re-run Step 2 command: all summary tests PASS.

- [ ] **Step 4: Write failing store tests**

Append to `tests/test_store.py`:

```python
@pytest.fixture
def mixed_store(tmp_path, mixed_xml):
    s = MaterialStore(db_path=tmp_path / "mixed.db")
    s.refresh(paths=[mixed_xml])
    yield s
    s.close()


def test_mixed_fixture_rows_and_types(mixed_store):
    rows = {r.id[-3:]: r for r in mixed_store.summaries(sort="id")}
    assert rows["004"].type == "btk"
    assert rows["005"].type == "construction"
    assert rows["004"].thick_min == 200


def test_type_filter(mixed_store):
    assert [r.id[-3:] for r in mixed_store.summaries(type="btk")] == ["004"]
    assert mixed_store.summaries(type="construction")[0].id.endswith("005")
    assert mixed_store.summaries(type="simple") == []
```

Run: expected FAIL (`summaries() got an unexpected keyword argument 'type'`).

NOTE on `sort="id"`: `_NUMERIC_SORTS/_STRING_SORTS` do not know "id"; extend `_STRING_SORTS` with `"id": "id"` while implementing Step 5 so this works deterministically.

- [ ] **Step 5: Implement store changes**

In `store.py`:
1. `SCHEMA_VERSION = "2"` (module constant; class attr follows automatically).
2. Add `"mtype"` to `_MATERIAL_COLUMNS` right after `"category"`.
3. In `_upsert_file`, add the value positionally to match: after `summary.category,` insert `summary.type,`.
4. In `summaries()` signature add `type=None` (after `category`) and in the filter builder: `if type: add("mtype=?", type)`.
5. Extend `_STRING_SORTS` with `"id": "id"`.

Run: full store tests PASS including v1->v2 rebuild path exercised implicitly by any pre-existing db in tmp dirs? Explicitly verify migration: open a db created BEFORE this change is impossible here; instead trust the existing tamper-test mechanism plus run one manual check:

```bash
python3 - <<'EOF'
import sqlite3, sys
sys.path.append("tests"); sys.path.append("src")
import tempfile, pathlib
from materialsdb.store import MaterialStore
tmp = pathlib.Path(tempfile.mkdtemp())
s = MaterialStore(db_path=tmp/"t.db")
s.connection.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
s.connection.commit(); s.close()
r = MaterialStore(db_path=tmp/"t.db")
print("rebuilt to:", r._get_meta("schema_version"))
assert r._get_meta("schema_version") == "2"
EOF
```

Expected output: `rebuilt to: 2`.

Also run FULL suite: characterization/store/query tests must stay green EXCEPT any that construct MaterialSummary positionally (there are none known - keyword-built everywhere).

- [ ] **Step 6: Gates + commit**

```bash
python3 -m pytest -p no:pytest-blender -q
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples && ruff format --exclude src/materialsdb/classes.py --check . && ty check --project .
git add src/materialsdb/summary.py src/materialsdb/store.py tests/
git commit -m "feat: type-aware summaries with mtype store column (schema v2)"
```

---

### Task 2: GUI server core + read endpoints

**Files:**
- Create: `src/materialsdb/gui/__init__.py` (empty), `src/materialsdb/gui/server.py`
- Create: `src/materialsdb/gui/static/index.html` (stub with `__TOKEN__` marker; full version in Task 4)
- Create: `tests/test_gui_server.py`

**Interfaces:**
- Consumes: store.summaries/get_summary (with type filter from Task 1)
- Produces:

```python
# server.py
class GuiState:
    def __init__(self, store=None): ...   # store=None -> lazy query.get_store()
    token: str                            # secrets.token_urlsafe(16)
    def resolve_store(self): ...

def make_server(state=None, port=0) -> ThreadingHTTPServer
    # binds 127.0.0.1; handler class bound to state; daemon threads
# routes GET: "/" (index w/ injected token), "/app.js", "/api/materials", "/api/materials/{id}"
def detail_payload(store_, material_id) -> dict | None
    # summary fields + extras: btk -> u_value_without [min,max] or None;
    # construction -> consref, designusage
```

- [ ] **Step 1: Write failing endpoint tests**

Create `tests/test_gui_server.py`:

```python
import http.client
import json
import threading

import pytest

pytest.importorskip("ifcopenshell")  # export/session tests in Task 3 need it; harmless here


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


def request(server, method, path, payload=None, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    headers = {}
    body = None
    if payload is not None:
        body = json.dumps(payload)
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["X-MaterialsDB-Token"] = token
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    content_type = response.getheader("Content-Type") or ""
    conn.close()
    if content_type.startswith("application/json"):
        return response.status, json.loads(data)
    return response.status, data


@pytest.fixture
def api(tmp_path, mini_xml):
    from materialsdb.gui.server import GuiState, make_server
    from materialsdb.store import MaterialStore

    store_ = MaterialStore(db_path=tmp_path / "gui.db")
    store_.refresh(paths=[mini_xml])
    state = GuiState(store=store_)
    server = make_server(state=state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, state
    server.shutdown()
    server.server_close()
    store_.close()


def test_index_serves_html_with_token(api):
    server, state = api
    status, body = request(server, "GET", "/")
    assert status == 200
    assert state.token.encode() in body
    assert b"__TOKEN__" not in body


def test_materials_list_and_filters(api):
    server, _ = api
    status, payload = request(server, "GET", "/api/materials")
    assert status == 200
    ids = {m["id"][-3:] for m in payload["materials"]}
    assert ids == {"001", "002", "003"}
    first = payload["materials"][0]
    assert {"id", "company", "category", "type", "names", "lambda_min"} <= set(first)

    status, payload = request(server, "GET", "/api/materials?category=Insulation")
    assert [m["id"][-3:] for m in payload["materials"]] == ["001"]

    status, payload = request(server, "GET", "/api/materials?type=btk&store_probe=1")
    assert payload["materials"] == []

    status, payload = request(server, "GET", "/api/materials?text=isol")
    assert len(payload["materials"]) == 1


def test_material_detail_with_btk_extras(api, mixed_xml):
    server, state = api
    state.store.refresh(paths=[mixed_xml])

    status, payload = request(server, "GET", "/api/materials/00000000-0000-0000-0000-000000000004")
    assert status == 200
    assert payload["type"] == "btk"
    assert payload["u_value_without"] == [0.18, 0.25]

    status, payload = request(server, "GET", "/api/materials/00000000-0000-0000-0000-000000000005")
    assert payload["consref"] == "REF-E"
    assert payload["designusage"] == "consDesignForWall"

    status, payload = request(server, "GET", "/api/materials/unknown")
    assert status == 404
```

The api fixture refreshes twice on purpose: mini first, then mixed incrementally (per-producer upsert adds without rebuilding).

Run: FAIL (`No module named 'materialsdb.gui'`).

- [ ] **Step 2: Implement server.py**

Create `src/materialsdb/gui/__init__.py` (empty file) and `src/materialsdb/gui/server.py`:

```python
"""Local web UI + HTTP API for exploring and exporting materialsdb materials."""
import http.server
import json
import secrets
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from materialsdb import config, utils

STATIC_DIR = Path(__file__).with_name("static")


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GuiState:
    def __init__(self, store=None):
        self.token = secrets.token_urlsafe(16)
        self.store = store
        self.session_path = None
        self.file = None

    def resolve_store(self):
        if self.store is not None:
            return self.store
        from materialsdb import query

        return query.get_store()


def detail_payload(store_, material_id):
    from materialsdb.classes import Material  # noqa: F401 - typing aid only

    summary = store_.get_summary(material_id)
    if summary is None:
        return None
    material = store_.get(material_id)
    payload = asdict(summary)
    country = config.get_country()
    if summary.type == "btk":
        u_values = []
        variations = getattr(getattr(material, "variations", None), "variation", ()) or ()
        for variation in variations:
            thermal = utils.get_by_country(variation.vthermal or (), country)
            if thermal is not None and thermal.U_value_without is not None:
                u_values.append(thermal.U_value_without)
        payload["u_value_without"] = [min(u_values), max(u_values)] if u_values else None
        payload.pop("lambda_min", None)
        payload.pop("lambda_max", None)
    elif summary.type == "construction":
        construction = getattr(material, "construction", None)
        payload["consref"] = str(getattr(construction, "consref", "") or "")
        payload["designusage"] = str(getattr(construction, "designusage", "") or "")
    return payload


class GuiHandler(http.server.BaseHTTPRequestHandler):
    state = None
    server_version = "materialsdb-gui"

    def log_message(self, *args):
        pass

    def _send(self, status, payload=None, content_type="application/json", raw=None):
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        return self.headers.get("X-MaterialsDB-Token") == self.state.token

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        store_ = self.state.resolve_store()
        if parsed.path == "/":
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", self.state.token)
            self._send(200, content_type="text/html; charset=utf-8", raw=html.encode("utf-8"))
            return
        if parsed.path == "/app.js":
            js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
            self._send(200, content_type="text/javascript; charset=utf-8", raw=js.encode("utf-8"))
            return
        if parsed.path == "/api/materials":
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            rows = store_.summaries(
                company=params.get("company") or None,
                category=params.get("category") or None,
                type=params.get("type") or None,
                min_lambda=_float(params.get("min_lambda")),
                max_lambda=_float(params.get("max_lambda")),
                min_thick=_float(params.get("min_thick")),
                max_thick=_float(params.get("max_thick")),
                usage=params.get("usage") or None,
                text=params.get("text") or None,
                sort=params.get("sort") or "company",
                ascending=params.get("order") != "desc",
                lang=params.get("lang") or None,
            )
            self._send(200, {"materials": [asdict(row) for row in rows]})
            return
        if parsed.path.startswith("/api/materials/"):
            material_id = parsed.path.rsplit("/", 1)[1]
            payload = detail_payload(store_, material_id)
            if payload is None:
                self._send(404, {"error": f"Unknown material id: {material_id}"})
            else:
                self._send(200, payload)
            return
        self._send(404, {"error": "not found"})


def make_server(state=None, port=0):
    state = state or GuiState()
    handler = type("BoundGuiHandler", (GuiHandler,), {"state": state})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    server.gui_state = state
    return server
```

Create the stub static page `src/materialsdb/gui/static/index.html`:

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>materialsdb picker</title></head>
<body>
<p>materialsdb picker placeholder</p>
<script>window.MATERIALSDB_TOKEN = "__TOKEN__";</script>
</body>
</html>
```

(`static/app.js` does not exist yet; the `/app.js` route returns 500 until Task 4 creates it - acceptable interim state, tests do not hit it yet.)

- [ ] **Step 3: Verify**

Run: `python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -v` then full suite.
Expected: all new tests PASS; full suite green.

- [ ] **Step 4: Commit**

```bash
git add src/materialsdb/gui tests/test_gui_server.py
git commit -m "feat: gui http server with read endpoints"
```

---

### Task 3: Action endpoints (export, session, pick, save, config, refresh)

**Files:**
- Modify: `src/materialsdb/gui/server.py`
- Modify: `tests/test_gui_server.py`

**Interfaces:**
- Consumes: Task 2 server; `add_material`, `create_material_file` NOT needed here; `ProjectLibrary.create_project_library(company, companyid, ver, crd)`; `utils.new_tdatetime`; `query.refresh`
- Produces:

```python
# POST routes on the same handler; all check _authorized() first (403), then:
POST /api/export        {ids: [..]}            -> raw .ifc attachment (multi-material wrapper)
POST /api/session/open  {path}                 -> {"path": ...} | 400 invalid
POST /api/pick          {ids: [..], replace}   -> {"added": n, "missing": [ids]} | 409 no session
POST /api/session/save  {path?}                -> {"saved": path} | 409 no session
POST /api/config        {lang?, country?}      -> {"ok": true}
POST /api/refresh       {force?}               -> {"existing": n, "updated": [...], "deleted": [...], "skipped": [...]}
```

- [ ] **Step 1: Write failing tests**

Append to `tests/test_gui_server.py`:

```python
def test_mutations_require_token(api):
    server, state = api
    status, payload = request(server, "POST", "/api/refresh", payload={})
    assert status == 403

    status, payload = request(server, "POST", "/api/refresh", payload={}, token="wrong")
    assert status == 403


def test_export_multi_material_roundtrip(api, tmp_path):
    import ifcopenshell

    server, state = api
    status, body = request(
        server,
        "POST",
        "/api/export",
        payload={"ids": ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"]},
        token=state.token,
    )
    assert status == 200
    out = tmp_path / "export.ifc"
    out.write_bytes(body)
    reopened = ifcopenshell.open(str(out))
    names = sorted(m.Name for m in reopened.by_type("IfcMaterial"))
    assert names == ["Beton B", "Isolant A", "Isolant A"]


def test_export_requires_ids(api):
    server, state = api
    status, payload = request(server, "POST", "/api/export", payload={"ids": []}, token=state.token)
    assert status == 400


def test_session_open_pick_save_flow(api, tmp_path):
    import ifcopenshell

    from materialsdb.ifc.material_builder import create_material_file

    # a target file to append into: standalone file for material 2
    _, state = api
    store_ = state.resolve_store()
    from materialsdb.ifc.material_builder import create_material_file

    target = create_material_file("00000000-0000-0000-0000-000000000002", store_=store_)
    target_path = tmp_path / "project.ifc"
    target.write(str(target_path))

    server, state = api
    token = state.token

    status, payload = request(server, "POST", "/api/session/open", payload={"path": str(target_path)}, token=token)
    assert status == 200

    status, payload = request(server, "POST", "/api/pick", payload={"ids": ["00000000-0000-0000-0000-000000000001"]}, token=token)
    assert status == 200 and payload["added"] == 1

    save_as = tmp_path / "project-plus.ifc"
    status, payload = request(server, "POST", "/api/session/save", payload={"path": str(save_as)}, token=token)
    assert status == 200

    reopened = ifcopenshell.open(str(save_as))
    assert {m.Name for m in reopened.by_type("IfcMaterial")} >= {"Isolant A", "Beton B"}


def test_pick_without_session_conflicts(api):
    server, state = api
    # fresh state without an open session
    from materialsdb.gui.server import GuiState

    _, fresh = api
    fresh.file = None
    fresh.session_path = None
    status, payload = request(
        server, "POST", "/api/pick",
        payload={"ids": ["00000000-0000-0000-0000-000000000001"]}, token=fresh.token,
    )
    assert status == 409


def test_session_open_rejects_bad_path(api, tmp_path):
    server, state = api
    status, payload = request(server, "POST", "/api/session/open", payload={"path": str(tmp_path / "nope.ifc")}, token=state.token)
    assert status == 400


def test_config_roundtrip(api):
    recorded = pytest.config_recorded

    status, _ = request(api[0], "POST", "/api/config", payload={"lang": "en", "country": "FR"}, token=api[1].token)

    assert status == 200
    assert recorded == {"lang": "en", "country": "FR"}
```

The summaries list carries ALL languages by design, so a language switch cannot change list payloads - it affects future detail resolution; hence this endpoint is verified as a config round-trip only. For hermeticity (set_lang would otherwise write the real user config.json), EXTEND the existing autouse fixture at the top of the file to record writes instead of performing them:

```python
@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")
    recorded = {}
    monkeypatch.setattr(
        "materialsdb.config.set_param",
        lambda param, value: recorded.__setitem__(param, value),
    )
    pytest.config_recorded = recorded
```

Run: FAIL (404 on all POST routes).

- [ ] **Step 2: Implement POST routes**

Add to `GuiHandler`:

```python
    def do_POST(self):
        parsed = urlparse(self.path)
        if not self._authorized():
            self._send(403, {"error": "forbidden"})
            return
        payload = self._read_json()
        store_ = self.state.resolve_store()
        try:
            if parsed.path == "/api/export":
                self._export(store_, payload)
            elif parsed.path == "/api/session/open":
                self._session_open(payload)
            elif parsed.path == "/api/pick":
                self._pick(store_, payload)
            elif parsed.path == "/api/session/save":
                self._session_save(payload)
            elif parsed.path == "/api/config":
                self._config(payload)
            elif parsed.path == "/api/refresh":
                self._refresh(payload)
            else:
                self._send(404, {"error": "not found"})
        except Exception as err:  # noqa: BLE001 - one bad request must not kill the server
            self._send(500, {"error": str(err)})

    def _export(self, store_, payload):
        import io
        import tempfile
        import uuid

        import ifcopenshell

        from materialsdb import utils
        from materialsdb.ifc.project_library import ProjectLibrary

        ids = payload.get("ids") or []
        if not ids:
            self._send(400, {"error": "ids required"})
            return
        library = ProjectLibrary()
        library.create_project_library(
            company="MaterialsDB Export",
            companyid=str(uuid.uuid4()),
            ver=1,
            crd=utils.new_tdatetime(),
        )
        added = []
        for material_id in ids:
            summary = store_.get_summary(material_id)
            material = store_.get(material_id)
            if summary is None or material is None:
                continue
            add_material(library.file, material, company_id=summary.company_id or "", company=summary.company)
            added.append(material_id)
        handle = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False)
        handle.close()
        library.file.write(handle.name)
        data = Path(handle.name).read_bytes()
        Path(handle.name).unlink(missing_ok=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/ifc")
        self.send_header("Content-Disposition", 'attachment; filename="materialsdb_export.ifc"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _session_open(self, payload):
        import ifcopenshell

        path = payload.get("path")
        candidate = Path(path) if path else None
        if candidate is None or not candidate.exists():
            self._send(400, {"error": f"path does not exist: {path}"})
            return
        try:
            self.state.file = ifcopenshell.open(str(candidate))
        except Exception as err:
            self._send(400, {"error": f"could not open ifc: {err}"})
            return
        self.state.session_path = candidate
        self._send(200, {"path": str(candidate)})

    def _pick(self, store_, payload):
        if self.state.file is None:
            self._send(409, {"error": "no session open"})
            return
        ids = payload.get("ids") or []
        if not ids:
            self._send(400, {"error": "ids required"})
            return
        replace = bool(payload.get("replace"))
        added = 0
        missing = []
        for material_id in ids:
            summary = store_.get_summary(material_id)
            material = store_.get(material_id)
            if summary is None or material is None:
                missing.append(material_id)
                continue
            add_material(
                self.state.file,
                material,
                company_id=summary.company_id or "",
                company=summary.company,
                replace=replace,
            )
            added += 1
        self._send(200, {"added": added, "missing": missing})

    def _session_save(self, payload):
        if self.state.file is None:
            self._send(409, {"error": "no session open"})
            return
        destination = Path(payload.get("path") or self.state.session_path)
        self.state.file.write(str(destination))
        self._send(200, {"saved": str(destination)})

    def _config(self, payload):
        from materialsdb import config

        lang = payload.get("lang")
        country = payload.get("country")
        if lang:
            config.set_lang(str(lang).lower())
        if country:
            config.set_country(str(country).upper())
        self._send(200, {"ok": True})

    def _refresh(self, payload):
        from materialsdb import query

        report = query.refresh(force=bool(payload.get("force")))
        self._send(
            200,
            {
                "existing": len(report.existing),
                "updated": [str(p) for p in report.updated],
                "deleted": [str(p) for p in report.deleted],
                "skipped": [str(p) for p in report.skipped],
            },
        )
```

Also add at top of server.py: `from pathlib import Path` (already present), and `add_material` import:

```python
from materialsdb.ifc.material_builder import add_material
```

(lazy inside methods is also fine if preferred for startup time; keep module-level - ifcopenshell is optional-but-present whenever gui export/session used; note in report if you choose lazy.)

Note `_export` uses `io` import listed but unused - drop `import io`.

- [ ] **Step 3: Verify + commit**

Full suite green; gates green.

```bash
git add src/materialsdb/gui/server.py tests/test_gui_server.py
git commit -m "feat: gui action endpoints for export sessions config refresh"
```

---

### Task 4: Frontend + entry point + packaging

**Files:**
- Modify: `src/materialsdb/gui/static/index.html` (replace stub)
- Create: `src/materialsdb/gui/static/app.js`
- Create: `src/materialsdb/gui/__main__.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: all endpoints from Tasks 2-3
- Produces: working `materialsdb-gui` command; served UI.

- [ ] **Step 1: Replace index.html**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>materialsdb picker</title>
<style>
 body { font-family: sans-serif; margin: 1rem; display: flex; gap: 1rem; }
 #left { flex: 3; } #right { flex: 2; }
 table { border-collapse: collapse; width: 100%; }
 th, td { border: 1px solid #ccc; padding: .25rem .5rem; text-align: left; cursor: pointer; }
 tr.selected { background: #eef; }
 aside { position: sticky; top: 1rem; }
 dl dt { font-weight: bold; margin-top: .4rem; }
 label { display: block; margin-top: .5rem; }
 input, select { margin-right: .5rem; }
 button { margin: .5rem .25rem 0 0; }
 #status { color: #06c; min-height: 1.2em; }
</style>
</head>
<body>
<div id="left">
 <div>
  <input id="text" placeholder="search"><select id="type">
   <option value="">type</option><option>simple</option><option>btk</option><option>construction</option>
  </select>
  <select id="sort">
   <option value="company">company</option><option value="category">category</option>
   <option value="lambda">lambda</option><option value="thick">thickness</option><option value="name">name</option>
  </select>
  <label style="display:inline">desc <input type="checkbox" id="desc"></label>
  <button onclick="loadTable()">search</button>
  <button id="export">export .ifc</button>
  <button id="open">open session...</button>
  <button id="pick">append selected</button>
  <label style="display:inline">replace <input type="checkbox" id="replace"></label>
  <button id="save">save session</button>
  <button id="refresh">refresh cache</button>
 </div>
 <p id="status"></p>
 <table><thead><tr><th></th><th>name</th><th>company</th><th>category</th><th>type</th><th>lambda</th><th>thick mm</th><th>usage</th></tr></thead>
 <tbody id="rows"></tbody></table>
</div>
<aside id="right"><h3>detail</h3><dl id="detail"></dl></aside>
<script>window.MATERIALSDB_TOKEN = "__TOKEN__";</script>
<script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write app.js**

```javascript
const TOKEN = window.MATERIALSDB_TOKEN;
const $ = (id) => document.getElementById(id);
const selected = new Set();

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-MaterialsDB-Token": TOKEN },
  });
  const type = response.headers.get("Content-Type") || "";
  if (!response.ok) {
    const message = type.includes("json") ? (await response.json()).error : response.statusText;
    throw new Error(message);
  }
  return type.includes("json") ? response.json() : response.blob();
}

function fmt(range, digits = 3) {
  if (range === null || range === undefined) return "";
  return Array.isArray(range)
    ? `${Number(range[0]).toFixed(digits)} - ${Number(range[1]).toFixed(digits)}`
    : Number(range).toFixed(digits);
}

async function loadTable() {
  const params = new URLSearchParams();
  if ($("text").value) params.set("text", $("text").value);
  if ($("type").value) params.set("type", $("type").value);
  params.set("sort", $("sort").value);
  if ($("desc").checked) params.set("order", "desc");
  const { materials } = await api(`/api/materials?${params}`);
  const rows = $("rows");
  rows.innerHTML = "";
  materials.forEach((m) => {
    const tr = document.createElement("tr");
    const usage = Object.entries(m.usage).filter(([, v]) => v).map(([k]) => k[0].toUpperCase()).join("");
    tr.innerHTML = `<td><input type="checkbox"></td><td></td><td>${m.company}</td>` +
      `<td>${m.category}</td><td>${m.type}</td>` +
      `<td>${fmt([m.lambda_min, m.lambda_max])}</td><td>${fmt([m.thick_min, m.thick_max], 0)}</td><td>${usage}</td>`;
    const [checkbox] = tr.getElementsByTagName("input");
    checkbox.onchange = () => (checkbox.checked ? selected.add(m.id) : selected.delete(m.id));
    tr.addEventListener("click", (event) => {
      if (event.target.tagName === "INPUT") return;
      document.querySelectorAll("tr.selected").forEach((el) => el.classList.remove("selected"));
      tr.classList.add("selected");
      showDetail(m.id);
    });
    rows.appendChild(tr);
  });
  setStatus(`${materials.length} materials`);
}

async function showDetail(id) {
  const m = await api(`/api/materials/${id}`);
  const pairs = [["id", m.id], ["names", JSON.stringify(m.names)], ["descriptions", JSON.stringify(m.descriptions)],
    ["company", `${m.company} (${m.company_id})`], ["category", m.category], ["type", m.type],
    ["lambda", fmt([m.lambda_min, m.lambda_max])], ["thickness mm", fmt([m.thick_min, m.thick_max], 0)],
    ["U-value", fmt(m.u_value_without)], ["consref", m.consref], ["design usage", m.designusage]];
  $("detail").innerHTML = pairs.filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<dt>${k}</dt><dd>${String(v)}</dd>`).join("");
}

async function pickIds(action) {
  if (!selected.size) return setStatus("select at least one material");
  if (action === "export") {
    const blob = await api("/api/export", { method: "POST", body: JSON.stringify({ ids: [...selected] }) });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "materialsdb_export.ifc"; link.click();
    URL.revokeObjectURL(url);
  } else if (action === "pick") {
    const result = await api("/api/pick", { method: "POST",
      body: JSON.stringify({ ids: [...selected], replace: $("replace").checked }) });
    setStatus(`appended ${result.added}, missing ${result.missing.length}`);
  }
}

async function openSession() {
  const path = prompt("path to existing .ifc to append into:");
  if (!path) return;
  const result = await api("/api/session/open", { method: "POST", body: JSON.stringify({ path }) });
  setStatus(`session open: ${result.path}`);
}

async function saveSession() {
  const path = prompt("save as (blank = original path):");
  const result = await api("/api/session/save", { method: "POST", body: path ? JSON.stringify({ path }) : "{}" });
  setStatus(`saved ${result.saved}`);
}

function setStatus(text) { $("status").textContent = text; return text; }

$("export").onclick = () => pickIds("export").catch((err) => setStatus(err.message));
$("pick").onclick = () => pickIds("pick").catch((err) => setStatus(err.message));
$("open").onclick = () => openSession().catch((err) => setStatus(err.message));
$("save").onclick = () => saveSession().catch((err) => setStatus(err.message));
$("refresh").onclick = async () => {
  const report = await api("/api/refresh", { method: "POST", body: "{}" });
  setStatus(`cache refreshed: ${report.existing} unchanged, ${report.updated.length} updated`);
};

loadTable();
```

- [ ] **Step 3: Entry point**

Create `src/materialsdb/gui/__main__.py`:

```python
"""Entry point for the materialsdb picker web UI."""
import argparse
import webbrowser

from materialsdb.gui.server import make_server


def main():
    parser = argparse.ArgumentParser(prog="materialsdb-gui", description="Explore and export materialsdb materials.")
    parser.add_argument("--port", type=int, default=8619, help="local port (default 8619)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()

    server = make_server(port=args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"materialsdb picker on {url} (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Packaging**

In `pyproject.toml` add inside `[project]`:

```toml
[project.scripts]
materialsdb-gui = "materialsdb.gui.__main__:main"
```

and extend package-data:

```toml
[tool.setuptools.package-data]
"*" = ["*.json", "*.xsd", "*.html", "*.js"]
```

- [ ] **Step 5: Verify + commit**

```bash
python3 -m pytest -p no:pytest-blender -q
python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -v
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples && ruff format --exclude src/materialsdb/classes.py --check .
ty check --project .
rm -rf /tmp/opencode/dist-gui && python -m build --outdir /tmp/opencode/dist-gui >/dev/null && python -m zipfile -l /tmp/opencode/dist-gui/*.whl | grep -E "static/(index.html|app.js)"
pip install -e ".[ifc,dev]" -q && command -v materialsdb-gui && timeout 3 materialsdb-gui --port 8765 --no-browser &
sleep 1; curl -s http://127.0.0.1:8765/ | head -c 120; kill %1 2>/dev/null
```

Expected: suite green; wheel ships static assets; console script exists; server serves HTML containing the token.

Commit:

```bash
git add src/materialsdb/gui pyproject.toml
git commit -m "feat: materialsdb-gui web frontend and console script"
```

---

### Task 5: README + final gates

**Files:**
- Modify: `README.md`

**Interfaces:** docs only.

- [ ] **Step 1: README section**

Insert after `# Create a single material in IFC :` (before `# How to install`), matching heading style:

```markdown
# Material picker GUI :
Launch the local web application (stdlib only, no extra dependencies):

```bash
materialsdb-gui            # opens http://127.0.0.1:8619 in your browser
```

Browse, sort and filter all cached materials; multi-select then either export
a standalone `.ifc`, or open one of your own `.ifc` files and append the
selected materials into it. The same HTTP API powers future BIM software
plugins (all mutating calls require a per-launch token).
```

(Adjust fence nesting per README conventions used by earlier sections.)

- [ ] **Step 2: Final verification**

All four gates green; full suite green; quick manual smoke:

```bash
timeout 3 materialsdb-gui --port 8671 --no-browser & sleep 1; curl -s http://127.0.0.1:8671/api/materials | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['materials']), 'materials listed')"
kill %1 2>/dev/null
```

Expected: prints your local material count (~2300 with a populated cache).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: material picker gui readme section"
```
