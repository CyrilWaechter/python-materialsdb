# Bonsai Integration — Transport + Slice 1 (materials push) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GUI-driven push channel between materialsdb-gui and a Bonsai-side Blender extension, plus slice 1: send picked materials from the picker into the active Bonsai model as registered `IfcMaterial` entities.

**Architecture:** The stdlib GUI server gains an in-memory listener registry plus five `/api/listener/*` endpoints (register / poll / send / status / clients). The Bonsai add-on polls every ~1s with its own stdlib HTTP client, receives fully-resolved JSON payloads (server-side resolution mirroring `MaterialBuilder`), and maps them onto the active IFC file through a pure-ifcopenshell module. Spec: `docs/superpowers/specs/2026-08-27-bonsai-integration-transport-design.md`.

**Tech Stack:** stdlib `http.server` (server), stdlib `http.client` (add-on client), `ifcopenshell` (add-on insert + CI tests), Blender 4.2+ extension format (`blender_manifest.toml`), vanilla JS (picker UI).

## Global Constraints

- Python `>=3.10`; **no new runtime dependencies** for the pip package (server stays stdlib-only); the add-on depends only on bpy/bonsai/ifcopenshell (bundled with Bonsai).
- Formatter/linter is **ruff** (NOT black), line-length 120. Local pytest: `python3 -m pytest -p no:pytest-blender -q` (CI uses plain `pytest`).
- Every mutating `/api/listener/*` call is token-gated (`X-MaterialsDB-Token`) like all other endpoints.
- Listener stale threshold: **5s** without polls; add-on poll interval **~1s** (`bpy.app.timers`).
- Payload contract: **one entry per `IfcMaterial`** (2-layer material → 2 entries), values exactly mirroring `MaterialBuilder.build()` (same `PSETS` resolution, unit factors, configured country).
- `bonsai_addon/` is **excluded from ty** (bpy/bonsai unresolvable; `tool.ty.src.include` lists explicit dirs — do not add it there) but **linted by ruff** (add to check paths).
- Discovery file: `<cache.get_cache_folder()>/gui.json` — never hardcode `~/.cache`.
- One pending payload per client; `poll` returns and clears it (at-most-once).

---

### Task 1: Listener registry + endpoints

**Files:**
- Modify: `src/materialsdb/gui/server.py` (imports, `GuiState.__init__`, `do_GET`, `do_POST`, new methods)
- Test: `tests/test_gui_server.py`

**Interfaces:**
- Consumes: existing `GuiState`, `GuiHandler._send`, token gate `_authorized()`, `parse_qs` (already imported in server.py).
- Produces: `GuiState.listeners` dict `client_id -> {"model_path": str, "last_seen": float, "pending": dict | None, "last_status": dict | None}`; endpoints `POST /api/listener/register|send|status`, `GET /api/listener/poll?client_id=X` (204 when empty), `GET /api/listener/clients`. Task 2 changes `send` to resolve payloads; Task 4's JS consumes `clients`/`send`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_server.py`:

```python
def test_listener_register_poll_send_roundtrip(api):
    server, state = api
    status, _ = request(
        server, "POST", "/api/listener/register",
        payload={"client_id": "c1", "model_path": "/tmp/model.ifc"}, token=state.token,
    )
    assert status == 200

    # empty poll -> 204, keeps listener alive
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    conn.request("GET", "/api/listener/poll?client_id=c1", headers={"X-MaterialsDB-Token": state.token})
    response = conn.getresponse()
    response.read()
    conn.close()
    assert response.status == 204

    # send queues, next poll returns and clears it
    status, body = request(
        server, "POST", "/api/listener/send",
        payload={"client_id": "c1", "action": "add_materials", "items": [{"id": "00000000-0000-0000-0000-000000000001"}]},
        token=state.token,
    )
    assert status == 200
    assert body["queued"] >= 1  # Task 2 tightens this to == 2 (one entry per layer)
    assert body["missing"] == []

    status, body = request(server, "GET", "/api/listener/poll?client_id=c1", token=state.token)
    assert status == 200
    assert body["payload"]["action"] == "add_materials"

    status, body = request(server, "GET", "/api/listener/poll?client_id=c1", token=state.token)
    assert status == 204  # cleared


def test_listener_send_unknown_client_404(api):
    server, state = api
    status, body = request(
        server, "POST", "/api/listener/send",
        payload={"client_id": "ghost", "action": "add_materials", "items": [{"id": "x"}]},
        token=state.token,
    )
    assert status == 404
    assert "ghost" in body["error"]


def test_listener_requires_token(api):
    server, state = api
    assert request(server, "POST", "/api/listener/register", payload={"client_id": "c"})[0] == 403
    assert request(server, "GET", "/api/listener/poll?client_id=c")[0] == 403


def test_listener_status_and_clients(api):
    server, state = api
    request(server, "POST", "/api/listener/register", payload={"client_id": "c1", "model_path": "/tmp/m.ifc"}, token=state.token)
    status, _ = request(
        server, "POST", "/api/listener/status",
        payload={"client_id": "c1", "status": "applied", "detail": "2 material(s)"}, token=state.token,
    )
    assert status == 200
    status, body = request(server, "GET", "/api/listener/clients", token=state.token)
    assert status == 200
    assert body["clients"] == [
        {"client_id": "c1", "model_path": "/tmp/m.ifc", "last_status": {"status": "applied", "detail": "2 material(s)"}}
    ]


def test_listener_stale_client_pruned(api, monkeypatch):
    server, state = api
    request(server, "POST", "/api/listener/register", payload={"client_id": "old", "model_path": ""}, token=state.token)
    listener = state.listeners["old"]
    listener["last_seen"] -= 10  # older than the 5s threshold
    status, body = request(server, "GET", "/api/listener/clients", token=state.token)
    assert status == 200
    assert body["clients"] == []
    assert "old" not in state.listeners
```

Note: the `queued >= 1` assertion is deliberately loose in Task 1 (the stub resolver echoes items 1:1); Task 2 replaces the stub and tightens the same test to `queued == 2`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -q`
Expected: FAIL — 404 on the new routes (router has no `/api/listener/*` branches).

- [ ] **Step 3: Implement the registry and endpoints**

In `src/materialsdb/gui/server.py`:

Add to top-level imports: `import time` (stdlib group).

`GuiState.__init__` — append:

```python
        # client_id -> {"model_path": str, "last_seen": float, "pending": dict | None, "last_status": dict | None}
        self.listeners = {}
```

Add a module-level constant after `STATIC_DIR`:

```python
_LISTENER_STALE_S = 5.0
```

Add methods to `GuiHandler` (place near the other `_`-prefixed helpers, before `do_GET`):

```python
    def _prune_listeners(self):
        now = time.time()
        stale = [cid for cid, l in self.state.listeners.items() if now - l["last_seen"] > _LISTENER_STALE_S]
        for cid in stale:
            del self.state.listeners[cid]

    def _listener_register(self, payload):
        client_id = str(payload.get("client_id") or "")
        if not client_id:
            self._send(400, {"error": "client_id required"})
            return
        self._prune_listeners()
        self.state.listeners[client_id] = {
            "model_path": str(payload.get("model_path") or ""),
            "last_seen": time.time(),
            "pending": None,
            "last_status": None,
        }
        self._send(200, {"ok": True})

    def _listener_poll(self, parsed):
        client_id = (parse_qs(parsed.query).get("client_id") or [""])[0]
        listener = self.state.listeners.get(client_id)
        if listener is None:
            self._send(404, {"error": f"unknown listener: {client_id}"})
            return
        listener["last_seen"] = time.time()
        payload = listener["pending"]
        listener["pending"] = None
        if payload is None:
            self._send_204()
            return
        self._send(200, {"payload": payload})

    def _send_204(self):
        try:
            self.send_response(204)
            self.end_headers()
        except ConnectionError:
            self.close_connection = True

    def _listener_send(self, store_, payload):
        client_id = str(payload.get("client_id") or "")
        listener = self.state.listeners.get(client_id)
        if listener is None:
            self._send(404, {"error": f"unknown listener: {client_id}"})
            return
        items = payload.get("items") or []
        if not items:
            self._send(400, {"error": "items required"})
            return
        action = str(payload.get("action") or "add_materials")
        if action != "add_materials":
            self._send(400, {"error": f"unsupported action: {action}"})
            return
        resolved, missing = _resolve_add_materials(store_, items)
        if not resolved["materials"]:
            self._send(400, {"error": "nothing resolvable to send", "missing": missing})
            return
        listener["pending"] = resolved
        self._send(200, {"ok": True, "queued": len(resolved["materials"]), "missing": missing})

    def _listener_status(self, payload):
        client_id = str(payload.get("client_id") or "")
        listener = self.state.listeners.get(client_id)
        if listener is None:
            self._send(404, {"error": f"unknown listener: {client_id}"})
            return
        listener["last_status"] = {
            "status": str(payload.get("status") or "error"),
            "detail": str(payload.get("detail") or ""),
        }
        self._send(200, {"ok": True})

    def _listener_clients(self):
        self._prune_listeners()
        clients = [
            {"client_id": cid, "model_path": l["model_path"], "last_status": l["last_status"]}
            for cid, l in sorted(self.state.listeners.items())
        ]
        self._send(200, {"clients": clients})
```

For Task 1, add this module-level stub resolver right after `_resolve_display_name` (Task 2 replaces it with the real one in `listener.py`):

```python
def _resolve_add_materials(store_, items):
    """Task-1 stub: echo items; replaced by gui.listener in Task 2."""
    return {"action": "add_materials", "materials": [{"item": i} for i in items]}, []
```

Adjust the `queued == 2` expectation concern: with the stub, Task 1's first test would see `queued == 1`. Therefore, in **this task**, write the roundtrip test's send assertions as:

```python
    assert status == 200
    assert body["queued"] >= 1
    assert body["missing"] == []
```

and add the strict `queued == 2` assertion in Task 2's test step (the same test is extended there).

Routing — in `do_POST`, add before the final `else`:

```python
            elif parsed.path == "/api/listener/register":
                self._listener_register(payload)
            elif parsed.path == "/api/listener/send":
                self._listener_send(store_, payload)
            elif parsed.path == "/api/listener/status":
                self._listener_status(payload)
```

In `do_GET`, add directly after the `/api/config` block (before the constructions routes):

```python
        if parsed.path == "/api/listener/poll":
            self._listener_poll(parsed)
            return
        if parsed.path == "/api/listener/clients":
            self._listener_clients()
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -q`
Expected: PASS (all pre-existing tests + 5 new).

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/server.py tests/test_gui_server.py
git commit -m "feat(gui): listener registry with register/poll/send/status/clients endpoints"
```

---

### Task 2: Resolved `add_materials` payload

**Files:**
- Create: `src/materialsdb/gui/listener.py`
- Modify: `src/materialsdb/gui/server.py` (replace stub `_resolve_add_materials` with import from `listener.py`)
- Test: `tests/test_listener_payload.py`

**Interfaces:**
- Consumes: `MaterialStore.get/get_summary` (store API), `utils.get_material_name(material, lang)`, `utils.get_material_description(material, lang)`, `utils.get_material_layers(material)`, `utils.get_by_country(elements, country)`, `material_builder.PSETS` + `material_builder.get_value(layer, definition, country)`, `config.get_country()/get_lang()`.
- Produces: `build_add_materials_payload(store_, items, country=None, lang=None) -> tuple[dict, list[str]]` returning `({"action": "add_materials", "materials": [entry...]}, missing_ids)`. Entry shape (consumed by Task 5's `insert.py`): `{source_id, layer_id, name, description, category, color, identity: {material_id, company_id, company}, psets: {pset_name: {prop: value}}, layer: {layer_id, thick_m} | None}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_listener_payload.py`:

```python
import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell

from materialsdb.gui.listener import build_add_materials_payload
from materialsdb.ifc.material_builder import MATERIALSDB_PSET, MaterialBuilder
from materialsdb.store import MaterialStore


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


@pytest.fixture
def store(tmp_path, mini_xml):
    store_ = MaterialStore(db_path=tmp_path / "payload.db")
    store_.refresh(paths=[mini_xml])
    yield store_
    store_.close()


def test_payload_one_entry_per_layer(store):
    payload, missing = build_add_materials_payload(store, [{"id": "00000000-0000-0000-0000-000000000001"}])

    assert missing == []
    assert payload["action"] == "add_materials"
    assert len(payload["materials"]) == 2  # Isolant A has 2 layers
    first = payload["materials"][0]
    assert first["source_id"] == "00000000-0000-0000-0000-000000000001"
    assert first["name"] == "Isolant A"
    assert first["category"] == "Insulation"
    assert first["color"] == 16711680
    assert first["identity"] == {
        "material_id": "00000000-0000-0000-0000-000000000001",
        "company_id": "A1B85A67-5B1E-4960-A297-2DE8275049C5",
        "company": "Mini SA",
    }
    assert first["layer"]["layer_id"] == "00000000-0000-0000-0000-0000000000a1"
    assert first["layer"]["thick_m"] == 0.2  # 200 mm
    assert "materialsdb.org_layer" in first["psets"]


def test_payload_layer_subset(store):
    payload, missing = build_add_materials_payload(
        store, [{"id": "00000000-0000-0000-0000-000000000001", "layer_ids": ["00000000-0000-0000-0000-0000000000a2"]}]
    )

    assert missing == []
    assert len(payload["materials"]) == 1
    assert payload["materials"][0]["layer"]["layer_id"] == "00000000-0000-0000-0000-0000000000a2"
    assert payload["materials"][0]["layer"]["thick_m"] == 0.1


def test_payload_missing_and_layerless_ids(store):
    payload, missing = build_add_materials_payload(
        store,
        [{"id": "nope"}, {"id": "00000000-0000-0000-0000-000000000003"}],  # unknown + material 3 has no layers
    )

    assert payload["materials"] == []
    assert missing == ["nope", "00000000-0000-0000-0000-000000000003"]


def _builder_entity_map(file):
    """IfcMaterial STEP id -> {pset_name: {prop: value}} for its psets."""
    result = {}
    for pset in file.by_type("IfcMaterialProperties"):
        materials = pset.Material if isinstance(pset.Material, (list, tuple)) else [pset.Material]
        props = {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}
        for material in materials:
            result.setdefault(material.id(), {})[pset.Name] = props
    return result


def test_payload_matches_builder_output(store, mini_source):
    """Equivalence gate: resolver psets (names + values incl. unit factors) are
    exactly what MaterialBuilder writes for the same material."""
    material_id = "00000000-0000-0000-0000-000000000001"
    file = ifcopenshell.file(schema="IFC4")
    MaterialBuilder(file).build(
        mini_source.material[0],
        company_id="A1B85A67-5B1E-4960-A297-2DE8275049C5",
        company="Mini SA",
    )
    built = _builder_entity_map(file)

    payload, missing = build_add_materials_payload(store, [{"id": material_id}])
    assert missing == []
    assert len(payload["materials"]) == 2

    for entry in payload["materials"]:
        matches = [
            psets
            for psets in built.values()
            if psets.get(MATERIALSDB_PSET, {}).get("material_id") == material_id
            and psets.get("materialsdb.org_layer", {}).get("layer_id") == entry["layer_id"]
        ]
        assert len(matches) == 1
        # identity pset travels as the dedicated `identity` field, not in psets
        assert {k: v for k, v in matches[0].items() if k != MATERIALSDB_PSET} == entry["psets"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py -q`
Expected: FAIL — `ModuleNotFoundError: materialsdb.gui.listener`.

- [ ] **Step 3: Implement `listener.py` and wire it in**

Create `src/materialsdb/gui/listener.py`:

```python
"""Resolved push payloads for the listener channel (slice 1: add_materials).

The server resolves picker selections into exactly the entity list
MaterialBuilder would create (one entry per IfcMaterial); the Bonsai add-on
maps the payload onto its open file without importing this library."""

from materialsdb import config, utils
from materialsdb.ifc.material_builder import PSETS, get_value


def _resolved_psets(layer, country) -> dict:
    resolved = {}
    for pset_name, props in PSETS.items():
        values = {}
        for prop_name, definition in props.items():
            if not definition["primary_measure_type"]:
                continue
            value = get_value(layer, definition, country)
            if value:
                values[prop_name] = value * (definition.get("unit_factor") or 1)
        if values:
            resolved[pset_name] = values
    return resolved


def build_add_materials_payload(store_, items, country=None, lang=None):
    """Resolve picker items [{id, layer_ids?}] into
    ({"action": "add_materials", "materials": [...]}, missing_ids).

    One materials[] entry per IfcMaterial, mirroring MaterialBuilder.build():
    a 2-layer material yields 2 same-named entries, each carrying its own
    layer's resolved psets. Ids that fail to resolve (unknown, or no
    resolvable layers) land in missing_ids."""
    country = country or config.get_country()
    lang = lang or config.get_lang()
    materials = []
    missing = []
    for item in items:
        if not isinstance(item, dict):
            missing.append(str(item))
            continue
        material_id = str(item.get("id") or "")
        summary = store_.get_summary(material_id)
        material = store_.get(material_id)
        if summary is None or material is None:
            missing.append(material_id)
            continue
        wanted = {str(g) for g in item.get("layer_ids") or []} or None
        name = str(utils.get_material_name(material, lang))
        description = str(utils.get_material_description(material, lang))
        category = str(getattr(material.information, "group", "") or "")
        color = getattr(material.information, "color", None)
        entries = []
        for layer in utils.get_material_layers(material):
            if wanted is not None and str(layer.id) not in wanted:
                continue
            geometry = utils.get_by_country(layer.geometry or (), country)
            thick = getattr(geometry, "thick", None)
            entries.append(
                {
                    "source_id": material_id,
                    "layer_id": str(layer.id),
                    "name": name,
                    "description": description,
                    "category": category,
                    "color": int(color) if color else None,
                    "identity": {
                        "material_id": material_id,
                        "company_id": str(summary.company_id or ""),
                        "company": str(summary.company or ""),
                    },
                    "psets": _resolved_psets(layer, country),
                    "layer": {"layer_id": str(layer.id), "thick_m": thick / 1000} if thick else None,
                }
            )
        if not entries:
            missing.append(material_id)
            continue
        materials.extend(entries)
    return {"action": "add_materials", "materials": materials}, missing
```

In `src/materialsdb/gui/server.py`, delete the Task-1 stub `_resolve_add_materials` and change `_listener_send`'s resolution call to:

```python
        from materialsdb.gui.listener import build_add_materials_payload

        resolved, missing = build_add_materials_payload(store_, items)
```

(keep the `if not resolved["materials"]:` guard unchanged).

Extend the Task-1 roundtrip test's send block to the strict form:

```python
    assert body["queued"] == 2  # Isolant A has 2 layers -> 2 IfcMaterial entries
    assert body["missing"] == []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_listener_payload.py tests/test_gui_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/listener.py src/materialsdb/gui/server.py tests/test_listener_payload.py tests/test_gui_server.py
git commit -m "feat(gui): resolve add_materials payloads mirroring MaterialBuilder output"
```

---

### Task 3: Discovery file (server side)

**Files:**
- Create: `src/materialsdb/gui/discovery.py`
- Modify: `src/materialsdb/gui/__main__.py`
- Test: `tests/test_gui_server.py` (append discovery tests)

**Interfaces:**
- Consumes: `cache.get_cache_folder()`.
- Produces: `listener_info_path() -> Path`, `write_listener_info(port: int, token: str) -> Path`, `remove_listener_info() -> None`. The add-on (Task 6) reads the same JSON shape `{port, token, pid}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_server.py`:

```python
def test_discovery_write_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr("materialsdb.cache.get_cache_folder", lambda: tmp_path)

    from materialsdb.gui import discovery

    path = discovery.write_listener_info(8619, "tok123")
    assert path == tmp_path / "gui.json"
    import json

    assert json.loads(path.read_text(encoding="utf-8")) == {"port": 8619, "token": "tok123", "pid": os.getpid()}

    discovery.remove_listener_info()
    assert not path.exists()
    discovery.remove_listener_info()  # idempotent
```

Add `import os` to the test module's stdlib import group (after `import json`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -p no:pytest-blender tests/test_gui_server.py::test_discovery_write_and_remove -q`
Expected: FAIL — `ImportError: cannot import name 'discovery'`.

- [ ] **Step 3: Implement**

Create `src/materialsdb/gui/discovery.py`:

```python
"""Discovery file bridging the GUI server and local listeners (Bonsai add-on).

The GUI writes {port, token, pid} into the shared cache folder at startup
and removes it on clean shutdown; listeners read it to authenticate. The
path comes from cache.get_cache_folder() (APPDATA / XDG_CACHE_HOME aware)."""

import json
import os
from pathlib import Path

from materialsdb import cache


def listener_info_path() -> Path:
    return cache.get_cache_folder() / "gui.json"


def write_listener_info(port: int, token: str) -> Path:
    path = listener_info_path()
    path.write_text(
        json.dumps({"port": int(port), "token": token, "pid": os.getpid()}),
        encoding="utf-8",
    )
    return path


def remove_listener_info() -> None:
    listener_info_path().unlink(missing_ok=True)
```

Modify `src/materialsdb/gui/__main__.py`:

```python
"""Entry point for the materialsdb picker web UI."""

import argparse
import webbrowser

from materialsdb.gui.discovery import remove_listener_info, write_listener_info
from materialsdb.gui.server import make_server


def main():
    parser = argparse.ArgumentParser(prog="materialsdb-gui", description="Explore and export materialsdb materials.")
    parser.add_argument("--port", type=int, default=8619, help="local port (default 8619)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args()

    server = make_server(port=args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    write_listener_info(server.server_address[1], server.gui_state.token)
    print(f"materialsdb picker on {url} (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        remove_listener_info()
        server.server_close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/discovery.py src/materialsdb/gui/__main__.py tests/test_gui_server.py
git commit -m "feat(gui): write discovery file (port/token/pid) for local listeners"
```

---

### Task 4: Picker "send to Bonsai" UI

**Files:**
- Modify: `src/materialsdb/gui/static/index.html`
- Modify: `src/materialsdb/gui/static/app.js`

**Interfaces:**
- Consumes: `GET /api/listener/clients` (`{clients: [{client_id, model_path, last_status}]}`), `POST /api/listener/send` (`{client_id, action, items}` → `{queued, missing}`), existing `selected`/`layerSelections` state and `pickIds` items shape `[{id, layer_ids?}]`.
- Produces: none (UI only).

- [ ] **Step 1: Add the controls to `index.html`**

In the button row (after the `<button id="refresh">refresh cache</button>` line), add:

```html
  <select id="bonsai-target" style="display:none"></select>
  <button id="send-bonsai" disabled>send to Bonsai</button>
```

- [ ] **Step 2: Wire the JS in `app.js`**

Add near the other session helpers (after `saveSession`, before `setStatus`):

```javascript
let bonsaiClients = [];

async function refreshBonsaiClients() {
  try {
    const { clients } = await api("/api/listener/clients");
    bonsaiClients = clients;
  } catch {
    bonsaiClients = [];
  }
  const sel = $("bonsai-target");
  sel.style.display = bonsaiClients.length ? "inline" : "none";
  $("send-bonsai").disabled = !bonsaiClients.length;
  const previous = sel.value;
  sel.innerHTML = bonsaiClients.map((c) => {
    const label = c.model_path ? c.model_path.split(/[\\/]/).pop() : "Bonsai";
    const mark = c.last_status ? (c.last_status.status === "applied" ? " \u2713" : " \u2717") : "";
    return `<option value="${esc(c.client_id)}">${esc(label)}${mark}</option>`;
  }).join("");
  if (bonsaiClients.some((c) => c.client_id === previous)) sel.value = previous;
}

async function sendToBonsai() {
  if (!selected.size) return setStatus("select at least one material");
  const client_id = $("bonsai-target").value || bonsaiClients[0]?.client_id;
  if (!client_id) return setStatus("no Bonsai listener connected");
  const items = [...selected].map((id) => {
    const layers = layerSelections.get(id);
    return layers && layers.size ? { id, layer_ids: [...layers] } : { id };
  });
  const result = await api("/api/listener/send", {
    method: "POST",
    body: JSON.stringify({ client_id, action: "add_materials", items }),
  });
  setStatus(`sent to Bonsai: ${result.queued} material(s)${result.missing.length ? `, missing ${result.missing.length}` : ""}`);
}
```

Add to the handler block (after the `$("refresh").onclick` handler):

```javascript
$("send-bonsai").onclick = () => sendToBonsai().catch((err) => setStatus(err.message));
setInterval(refreshBonsaiClients, 2000);
refreshBonsaiClients();
```

- [ ] **Step 3: Syntax-check the JS**

Run: `node --check src/materialsdb/gui/static/app.js`
Expected: exit 0.

- [ ] **Step 4: Manual checklist (no automated frontend tests, per project practice)**

1. `PYTHONPATH=src python3 -m materialsdb.gui --no-browser`, open the picker page.
2. Without any listener: "send to Bonsai" disabled, no target select visible.
3. Select materials (whole + per-layer) → button enabled → click → status shows "sent to Bonsai: N material(s)" (N = sum of layers).
4. Unknown-state checks: sending with zero selection shows "select at least one material".

- [ ] **Step 5: Commit**

```bash
git add src/materialsdb/gui/static/index.html src/materialsdb/gui/static/app.js
git commit -m "feat(gui): send picked materials to a connected Bonsai listener"
```

---

### Task 5: Add-on `insert.py` (pure ifcopenshell mapping)

**Files:**
- Create: `bonsai_addon/insert.py`
- Test: `tests/test_bonsai_insert.py`

**Interfaces:**
- Consumes: Task 2 payload entry shape (`identity`, `psets` incl. `materialsdb.org_layer` with `layer_id`, `layer: {layer_id, thick_m} | None`, `color`).
- Produces: `apply_add_materials(file, payload) -> int` (number of materials created). Idempotence key: `(materialsdb.material_id, materialsdb.org_layer.layer_id | None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bonsai_insert.py`:

```python
import sys
from pathlib import Path

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell

from materialsdb.gui.listener import build_add_materials_payload
from materialsdb.store import MaterialStore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bonsai_addon"))

from insert import apply_add_materials  # noqa: E402 - add-on module on a side path


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")


@pytest.fixture
def store(tmp_path, mini_xml):
    store_ = MaterialStore(db_path=tmp_path / "insert.db")
    store_.refresh(paths=[mini_xml])
    yield store_
    store_.close()


def _payload(store_):
    payload, missing = build_add_materials_payload(store_, [{"id": "00000000-0000-0000-0000-000000000001"}])
    assert missing == []
    return payload


def test_apply_creates_materials_psets_layers(store):
    file = ifcopenshell.file(schema="IFC4")

    created = apply_add_materials(file, _payload(store))

    assert created == 2
    assert len(file.by_type("IfcMaterial")) == 2
    identity = [p for p in file.by_type("IfcMaterialProperties") if p.Name == "materialsdb"]
    assert len(identity) == 2
    for pset in identity:
        props = {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}
        assert props["material_id"] == "00000000-0000-0000-0000-000000000001"
        assert props["company"] == "Mini SA"
    org_layers = [p for p in file.by_type("IfcMaterialProperties") if p.Name == "materialsdb.org_layer"]
    assert len(org_layers) == 2
    layer_ids = set()
    for pset in org_layers:
        props = {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}
        layer_ids.add(props["layer_id"])
    assert layer_ids == {"00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"}
    layers = sorted(file.by_type("IfcMaterialLayer"), key=lambda l: l.LayerThickness)
    assert [round(l.LayerThickness, 3) for l in layers] == [0.1, 0.2]
    assert {l.Description for l in layers} == {
        "00000000-0000-0000-0000-0000000000a1",
        "00000000-0000-0000-0000-0000000000a2",
    }
    assert len(file.by_type("IfcMaterialLayerSet")) == 2


def test_apply_is_idempotent(store):
    file = ifcopenshell.file(schema="IFC4")
    payload = _payload(store)

    assert apply_add_materials(file, payload) == 2
    counts = (len(file.by_type("IfcMaterial")), len(file.by_type("IfcMaterialLayer")))

    assert apply_add_materials(file, payload) == 0
    assert (len(file.by_type("IfcMaterial")), len(file.by_type("IfcMaterialLayer"))) == counts


def test_apply_layer_subset(store):
    file = ifcopenshell.file(schema="IFC4")
    payload, missing = build_add_materials_payload(
        store, [{"id": "00000000-0000-0000-0000-000000000001", "layer_ids": ["00000000-0000-0000-0000-0000000000a1"]}]
    )

    assert missing == []
    assert apply_add_materials(file, payload) == 1
    assert len(file.by_type("IfcMaterialLayer")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'insert'`.

- [ ] **Step 3: Implement `bonsai_addon/insert.py`**

```python
"""Map resolved add_materials payloads onto an ifcopenshell file.

Pure ifcopenshell (no bpy/bonsai imports) so CI can test it directly.
Creation mirrors materialsdb.ifc.material_builder.MaterialBuilder so
listener-inserted materials are indistinguishable from exported ones."""

import ifcopenshell  # noqa: F401 - documents the runtime requirement


def _materials_of(pset):
    materials = pset.Material
    if not isinstance(materials, (list, tuple)):
        materials = [materials]
    return materials


def _pset_props(pset):
    return {prop.Name: prop.NominalValue.wrappedValue for prop in pset.Properties}


def _existing_keys(file):
    """(material_id, layer_id | None) pairs already present, read from the
    materialsdb identity + org_layer psets."""
    layer_of = {}
    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != "materialsdb.org_layer":
            continue
        layer_id = _pset_props(pset).get("layer_id")
        for material in _materials_of(pset):
            layer_of[material.id()] = layer_id
    keys = set()
    for pset in file.by_type("IfcMaterialProperties"):
        if pset.Name != "materialsdb":
            continue
        material_id = _pset_props(pset).get("material_id")
        for material in _materials_of(pset):
            keys.add((material_id, layer_of.get(material.id())))
    return keys


def _ifc_text(file, value):
    return file.create_entity("IfcText", str(value))


def _nominal(file, value):
    if isinstance(value, bool):
        return file.create_entity("IfcBoolean", value)
    if isinstance(value, str):
        return _ifc_text(file, value)
    return file.create_entity("IfcReal", float(value))


def apply_add_materials(file, payload) -> int:
    """Create IfcMaterial (+ identity/property psets, layer + set, surface
    style) for each payload entry. Entries whose (material_id, layer_id) key
    is already present are skipped. Returns the number created."""
    existing = _existing_keys(file)
    created = 0
    for entry in payload.get("materials") or []:
        identity = entry["identity"]
        layer = entry.get("layer") or {}
        key = (identity["material_id"], layer.get("layer_id"))
        if key in existing:
            continue
        _apply_entry(file, entry)
        existing.add(key)
        created += 1
    return created


def _apply_entry(file, entry):
    material = file.createIfcMaterial(
        str(entry["name"]), str(entry.get("description") or ""), str(entry.get("category") or "")
    )
    identity = entry["identity"]
    file.create_entity(
        "IfcMaterialProperties",
        Name="materialsdb",
        Properties=[
            file.create_entity("IfcPropertySingleValue", Name="material_id", NominalValue=_ifc_text(file, identity["material_id"])),
            file.create_entity("IfcPropertySingleValue", Name="company_id", NominalValue=_ifc_text(file, identity.get("company_id") or "")),
            file.create_entity("IfcPropertySingleValue", Name="company", NominalValue=_ifc_text(file, identity.get("company") or "")),
        ],
        Material=material,
    )
    for pset_name, props in (entry.get("psets") or {}).items():
        properties = [
            file.create_entity("IfcPropertySingleValue", Name=str(name), NominalValue=_nominal(file, value))
            for name, value in props.items()
        ]
        file.create_entity("IfcMaterialProperties", Name=str(pset_name), Properties=properties, Material=material)
    layer = entry.get("layer")
    if layer:
        thickness = float(layer["thick_m"])
        label = f"{entry['name']} | {round(thickness * 1000)}mm"
        ifc_layer = file.create_entity(
            "IfcMaterialLayer",
            Material=material,
            LayerThickness=thickness,
            Name=label,
            Description=str(layer["layer_id"]),
        )
        file.create_entity("IfcMaterialLayerSet", MaterialLayers=[ifc_layer], LayerSetName=label)
    _apply_style(file, entry)


def _apply_style(file, entry):
    """Best-effort surface style from the raw XML color decimal; identical
    floating-chain shape to the export path (styled item without Item)."""
    color = entry.get("color")
    if not color:
        return
    try:
        color = int(color)
        shading = file.createIfcSurfaceStyleShading(
            SurfaceColour=file.createIfcColourRgb(Blue=color & 255, Green=(color >> 8) & 255, Red=(color >> 16) & 255)
        )
        style = file.createIfcSurfaceStyle(Name=f"color {color}", Side="BOTH", Styles=[shading])
        file.createIfcStyledItem(Styles=[style])
    except Exception:
        pass  # cosmetic; never fail the insert
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest -p no:pytest-blender tests/test_bonsai_insert.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bonsai_addon/insert.py tests/test_bonsai_insert.py
git commit -m "feat(bonsai): pure-ifcopenshell insert of resolved add_materials payloads"
```

---

### Task 6: Add-on shell (manifest, discovery, bpy wiring, build script, tooling)

**Files:**
- Create: `bonsai_addon/blender_manifest.toml`, `bonsai_addon/discovery.py`, `bonsai_addon/__init__.py`
- Create: `dev_utils/build_bonsai_addon.py`
- Modify: `.github/workflows/ci.yml`, `AGENTS.md`

**Interfaces:**
- Consumes: server endpoints from Task 1 (with token from Task 3's `gui.json`), `insert.apply_add_materials` from Task 5.
- Produces: installable extension zip `dist/materialsdb_listener.zip`; `bonsai_addon` ruff-linted (CI path extended), excluded from ty (no config change needed — `tool.ty.src.include` stays `["src", "tests", "dev_utils", "examples"]`).

- [ ] **Step 1: Manifest**

Create `bonsai_addon/blender_manifest.toml`:

```toml
schema_version = "1.0.0"

id = "materialsdb_listener"
version = "0.1.0"
name = "materialsdb listener"
tagline = "Receive materials pushed from the local materialsdb picker"
maintainer = "Cyril Waechter <cyrwae@hotmail.com>"
type = "add-on"
blender_version_min = "4.2.0"
license = ["SPDX:GPL-3.0-or-later"]
permissions = ["files", "network"]
```

- [ ] **Step 2: Add-on discovery client**

Create `bonsai_addon/discovery.py` (stdlib only, same cache-folder resolution as `materialsdb.cache`):

```python
"""Locate and authenticate to the local materialsdb-gui server.

Reads {port, token, pid} from the shared cache discovery file written by
materialsdb-gui at startup (same resolution as materialsdb.cache: APPDATA /
XDG_CACHE_HOME / ~/.cache)."""

import http.client
import json
import os
import uuid
from pathlib import Path


def _cache_folder():
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "materialsdb"


def read_gui_info():
    try:
        data = json.loads((_cache_folder() / "gui.json").read_text(encoding="utf-8"))
        return int(data["port"]), str(data["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


class ListenerClient:
    """Minimal HTTP client: register once per model, poll for pushes, report
    application status back to the GUI."""

    def __init__(self):
        self.client_id = str(uuid.uuid4())
        self.registered_path = None

    def _request(self, method, path, payload=None):
        info = read_gui_info()
        if info is None:
            raise ConnectionError("materialsdb-gui not reachable (no discovery file)")
        port, token = info
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"X-MaterialsDB-Token": token}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        if response.status == 204:
            return None
        if response.status != 200:
            raise RuntimeError(f"{method} {path} -> {response.status}: {data[:200]!r}")
        return json.loads(data) if data else None

    def register(self, model_path=""):
        self._request("POST", "/api/listener/register", {"client_id": self.client_id, "model_path": str(model_path or "")})
        self.registered_path = str(model_path or "")

    def poll(self):
        return self._request("GET", f"/api/listener/poll?client_id={self.client_id}")

    def report(self, status, detail=""):
        self._request("POST", "/api/listener/status", {"client_id": self.client_id, "status": status, "detail": str(detail or "")})
```

- [ ] **Step 3: bpy wiring**

Create `bonsai_addon/__init__.py`:

```python
"""materialsdb listener: receive pushes from the local materialsdb picker.

The bpy surface is intentionally thin: a timer polls the local GUI server,
and all IFC work goes through insert.apply_add_materials (pure ifcopenshell,
CI-tested)."""

import bpy

from . import insert
from .discovery import ListenerClient

_CLIENT = None


def _active_ifc_file():
    """The IFC file of the model currently open in Bonsai. Tries the modern
    bonsai tools API first, then the legacy IfcStore. Adjust here if your
    Bonsai version moved these entry points."""
    try:
        from bonsai.tool import ROOT

        return ROOT.get_active_model()
    except (ImportError, AttributeError):
        from bonsai.bim.ifc import IfcStore

        return IfcStore.get_file()


def _model_path():
    try:
        from bonsai.bim.ifc import IfcStore

        return str(getattr(IfcStore, "path", "") or "")
    except (ImportError, AttributeError):
        return ""


def _poll_timer():
    global _CLIENT
    if _CLIENT is None:
        return None  # listener stopped: unregister the timer
    try:
        path = _model_path()
        if path != _CLIENT.registered_path:
            _CLIENT.register(path)
        payload = _CLIENT.poll()
        if payload is not None:
            file = _active_ifc_file()
            if file is None:
                raise RuntimeError("no IFC model open in Bonsai")
            count = insert.apply_add_materials(file, payload)
            _CLIENT.report("applied", f"{count} material(s) added")
    except Exception as err:  # noqa: BLE001 - a failed push must not kill the timer
        try:
            _CLIENT.report("error", str(err))
        except Exception:
            pass
    return 1.0


class MATERIALSDB_OT_toggle_listener(bpy.types.Operator):
    bl_idname = "materialsdb.toggle_listener"
    bl_label = "materialsdb listener"
    bl_description = "Start or stop receiving pushes from the local materialsdb picker"
    bl_options = {"REGISTER"}

    def execute(self, context):
        global _CLIENT
        if _CLIENT is None:
            _CLIENT = ListenerClient()
            try:
                _CLIENT.register(_model_path())
            except Exception as err:  # noqa: BLE001
                _CLIENT = None
                self.report({"ERROR"}, f"materialsdb-gui not reachable: {err}")
                return {"CANCELLED"}
            bpy.app.timers.register(_poll_timer, persistent=True)
            self.report({"INFO"}, "materialsdb listener started")
        else:
            _CLIENT = None
            self.report({"INFO"}, "materialsdb listener stopped")
        return {"FINISHED"}


class MATERIALSDB_PT_panel(bpy.types.Panel):
    bl_label = "materialsdb"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "materialsdb"

    def draw(self, context):
        running = _CLIENT is not None
        layout = self.layout
        layout.operator("materialsdb.toggle_listener", text="Stop listener" if running else "Start listener")
        layout.label(text="listening" if running else "stopped", icon="LINK" if running else "UNLINKED")


def register():
    bpy.utils.register_class(MATERIALSDB_OT_toggle_listener)
    bpy.utils.register_class(MATERIALSDB_PT_panel)


def unregister():
    global _CLIENT
    _CLIENT = None
    if bpy.app.timers.is_registered(_poll_timer):
        bpy.app.timers.unregister(_poll_timer)
    bpy.utils.unregister_class(MATERIALSDB_PT_panel)
    bpy.utils.unregister_class(MATERIALSDB_OT_toggle_listener)
```

Note for the maintainer (Blender-only, cannot run in CI): the exact bonsai entry points in `_active_ifc_file`/`_model_path` must be validated against your Bonsai version during the manual test; the fallback chain above compiles against both known layouts.

- [ ] **Step 4: Build script**

Create `dev_utils/build_bonsai_addon.py`:

```python
"""Zip bonsai_addon/ as a Blender extension for 'Install from Disk'.

The manifest must sit at the zip root, so archive names are relative to
bonsai_addon/ itself."""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bonsai_addon"
OUT = ROOT / "dist" / "materialsdb_listener.zip"


def main():
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(SRC.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                zf.write(file, file.relative_to(SRC))
    print(OUT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Tooling + CI wiring**

`.github/workflows/ci.yml` — change the ruff check line to include the add-on:

```yaml
          ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon
```

`AGENTS.md` — in the Commands block, change the lint line to:

```bash
ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon   # lint
```

and add to the "Packaging / schema notes" section:

```markdown
- `bonsai_addon/` is a Blender 4.2+ extension (Bonsai listener) — linted by ruff but **excluded from ty** (bpy/bonsai imports unresolvable; `tool.ty.src.include` deliberately omits it). Build the install zip with `python3 dev_utils/build_bonsai_addon.py` → `dist/materialsdb_listener.zip`. Its `insert.py` is pure ifcopenshell and CI-tested; the bpy/bonsai wiring in `__init__.py` is manual-checklist only.
```

Add `dist/` to `.gitignore`.

- [ ] **Step 6: Verify gates**

Run: `ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon && ruff format --exclude src/materialsdb/classes.py --check . && python3 dev_utils/build_bonsai_addon.py`
Expected: lint clean; format clean (run `ruff format` first if the new files need it); zip created.

Run: `python3 -m pytest -p no:pytest-blender -q && ty check --project .`
Expected: all tests PASS; ty clean (bonsai_addon untouched by ty).

- [ ] **Step 7: Commit**

```bash
git add bonsai_addon dev_utils/build_bonsai_addon.py .github/workflows/ci.yml AGENTS.md .gitignore
git commit -m "feat(bonsai): Blender extension shell with discovery client, bpy wiring, build script"
```

---

### Task 7: Docs + end-to-end manual verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: README section**

Add after the "Construction maker" section:

```markdown
# Bonsai integration :
Push materials straight from the picker into the IFC model open in
[Bonsai](https://bonsai.ifcopenshell.org). Install
`dist/materialsdb_listener.zip` (build it with
`python3 dev_utils/build_bonsai_addon.py`) via Blender's
*Extensions ▸ Install from Disk*, start the listener from the
materialsdb panel in the 3D-view sidebar, then run `materialsdb-gui` as
usual and hit *send to Bonsai* in the picker. Materials are registered
into the model's material library (identity + per-layer psets included);
assigning them to objects stays a normal Bonsai action.
```

- [ ] **Step 2: End-to-end manual checklist**

1. `python3 dev_utils/build_bonsai_addon.py`; install the zip in Blender (Bonsai installed).
2. Open/create an IFC project in Bonsai; sidebar ▸ materialsdb ▸ *Start listener* → status "listening"; Blender status shows the register succeeded.
3. `PYTHONPATH=src python3 -m materialsdb-gui`; picker shows the Bonsai target with the model name; send a whole material and a layer-picked one.
4. In Bonsai: materials appear (Bonsai material list), identity pset + org_layer pset values correct, surface color applied.
5. Send the same selection again → GUI reports `queued` but Bonsai gains no duplicates (idempotence).
6. Stop the GUI (Ctrl+C) → add-on reports unreachable quietly; restart GUI → send works again without restarting the listener.
7. Confirm `gui.json` exists while running and is gone after Ctrl+C.
8. (Maintainer) validate `_active_ifc_file`/`_model_path` against the installed Bonsai version; adjust if the API moved.

- [ ] **Step 3: Full gates**

Run: `python3 -m pytest -p no:pytest-blender -q && ruff check --exclude src/materialsdb/classes.py src tests dev_utils examples bonsai_addon && ruff format --exclude src/materialsdb/classes.py --check . && ty check --project .`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: Bonsai listener integration usage"
```
