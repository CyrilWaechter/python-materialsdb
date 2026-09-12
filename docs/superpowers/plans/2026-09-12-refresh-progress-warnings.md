# Refresh Progress + Cache Notices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Blocking progress overlay (with Cancel) for the cache refresh, plus empty-list and update-available notices in the GUI.

**Architecture:** `cache.py` gains a download-plan refactor with an `on_progress(done, total, name)` callback (truthy return = cancel request); the server runs the refresh in `GuiState`-owned background thread with `status`/`cancel` endpoints; the frontend overlay polls status and shows banners from a startup-only update check.

**Tech Stack:** Python stdlib (urllib, threading, http.server), vanilla JS, pytest with monkeypatched network fakes.

**Spec:** `docs/superpowers/specs/2026-09-12-refresh-progress-warnings-design.md`

## Global Constraints

- Tests: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender -q` (stale site-packages; threads in tests polled to completion with bounded wait loops, never real network).
- `ruff check --exclude src/materialsdb/classes.py src tests dev_utils bonsai_addon`; `ruff format --exclude "src/materialsdb/classes.py" --check .`; `ty check --project .` must pass at every commit gate.
- `make_server` must never trigger network (startup update check lives in `__main__.py` only).
- Network errors in the refresh job surface as `error` status + `"cache update failed: …"` message; no store wipe; test monkeypatching keeps CI offline.
- Do NOT touch `src/materialsdb/classes.py`.
- GUI copy software-agnostic; commit style `feat:`/`fix:`/`docs:`.

---

### Task 1: `cache.py` — plan-and-download with progress callback

**Files:**
- Modify: `src/materialsdb/cache.py` (functions `update_producers_data`, `update_producers_from_index`, lines 61-110; add `_index_plan`)
- Test: `tests/test_cache.py` (new file)

**Interfaces:**
- Consumes: existing `parse_cached_index`, `get_by_id`, `require_update`, `get_producers_dir`, `get_cached_index_path`, `Report`.
- Produces: `update_producers_data(url_list=MATERIALSDBINDEXURLLIST, on_progress=None) -> Report` — `on_progress(done, total, name)` called after each producer download with a GLOBAL running total; if the callback returns truthy, iteration stops between downloads and a partial Report is returned (contract used by Task 2's cancel). `update_producers_from_index(index) -> Report` keeps its signature (single-index wrapper). New public `updates_available(url_list=MATERIALSDBINDEXURLLIST) -> bool`: True when any company in the fresh indexes differs from the cached ones per `require_update` (network error propagates — the caller decides whether to swallow).

- [ ] **Step 1: Write the failing tests** — create `tests/test_cache.py`:

```python
import io
from pathlib import Path

import pytest

pytest.importorskip("lxml")

from lxml import etree


INDEX_XML = b"""<?xml version="1.0"?>
<companies>
<company id="A" href="https://x.org/a.xml" LastKnownDate="2026-01-01" KnownVersion="1"/>
<company id="B" href="https://x.org/b.xml" LastKnownDate="2026-01-01" KnownVersion="1"/>
</companies>"""

PRODUCER_XML = b"<materials><material id='m'/></materials>"


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    from materialsdb import cache

    cache_dir = tmp_path / "materialsdb"
    monkeypatch.setattr(cache, "get_cache_folder", lambda: cache_dir)
    recorded = {"urls": [], "dests": [], "index": INDEX_XML}

    def fake_urlopen(index):
        return io.BytesIO(recorded["index"])

    def fake_urlretrieve(url, dest):
        recorded["urls"].append(url)
        recorded["dests"].append(Path(dest).name)
        Path(dest).write_bytes(PRODUCER_XML)

    monkeypatch.setattr(cache.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cache.urllib.request, "urlretrieve", fake_urlretrieve)
    return cache, cache_dir, recorded


def test_first_refresh_downloads_all_with_global_total(cache_env):
    cache, cache_dir, recorded = cache_env
    progress = []
    report = cache.update_producers_data(
        on_progress=lambda done, total, name: progress.append((done, total, name)) or False
    )

    assert recorded["urls"] == ["https://x.org/a.xml", "https://x.org/b.xml"]
    assert progress == [(1, 2, "a.xml"), (2, 2, "b.xml")]
    assert [p.name for p in report.updated] == ["a.xml", "b.xml"]
    assert report.existing == []
    assert (cache_dir / "Producers" / "a.xml").exists()  # downloads land in Producers dir
    assert (cache_dir / "ProducersIndex.xml").exists()  # new index persisted


def test_second_refresh_uses_existing_files(cache_env):
    cache, cache_dir, recorded = cache_env
    report = cache.update_producers_data()
    assert [p.name for p in report.updated] == ["a.xml", "b.xml"]
    recorded["urls"].clear()

    progress = []
    report2 = cache.update_producers_data(
        on_progress=lambda done, total, name: progress.append((done, total, name)) or False
    )

    assert recorded["urls"] == []
    assert progress == []  # nothing to download → no callbacks
    assert [p.name for p in report2.existing] == ["a.xml", "b.xml"]
    assert report2.updated == []


def test_outdated_single_producer_updates_with_total_one(cache_env):
    cache, cache_dir, recorded = cache_env
    report = cache.update_producers_data()

    # age producer A in the cached index → next refresh must download only A
    index_path = cache_dir / "ProducersIndex.xml"
    tree = etree.parse(str(index_path))
    for company in tree.getroot():
        if company.get("id") == "A":
            company.set("LastKnownDate", "2025-01-01")
    tree.write(str(index_path))

    progress = []
    record = cache.update_producers_data(
        on_progress=lambda done, total, name: progress.append((done, total, name)) or False
    )

    assert recorded["urls"] == ["https://x.org/a.xml"]
    assert progress == [(1, 1, "a.xml")]
    assert [p.name for p in record.updated] == ["a.xml"]
    assert [p.name for p in record.deleted] == ["a.xml"]  # stale file removed before re-download
    assert [p.name for p in record.existing] == ["b.xml"]


def test_updates_available_matches_cached_index(cache_env):
    cache, cache_dir, recorded = cache_env
    assert cache.updates_available() is True  # empty cache: everything counts as new
    cache.update_producers_data()
    assert cache.updates_available() is False

    # bump the remote index → update available again
    INDEX_XML.alive = None  # placeholder removed; see note
```

Then replace the tail assertion by rewriting the remote index: define the fake urlopen per test to serve a NEWER xml — simplest: monkeypatch `cache.MATERIALSDBINDEXURLLIST` hmm not needed; the fixture's `fake_urlopen` ignores the url. Give the fixture a mutable payload: `recorded["index"] = INDEX_XML`, `fake_urlopen` returns `io.BytesIO(recorded["index"])`; in this test:

```python
def test_updates_available_after_remote_change(cache_env):
    cache, cache_dir, recorded = cache_env
    cache.update_producers_data()
    assert cache.updates_available() is False

    recorded["index"] = INDEX_XML.replace(b'KnownVersion="1"\n<company id="B"', b'KnownVersion="2"\n<company id="B"')
```

That replacement is fragile — instead make the bump explicit and deterministic:

```python
def test_updates_available_after_remote_change(cache_env):
    cache, cache_dir, recorded = cache_env
    cache.update_producers_data()
    assert cache.updates_available() is False

    bumped = INDEX_XML.replace(
        b'<company id="B" href="https://x.org/b.xml" LastKnownDate="2026-01-01" KnownVersion="1"/>',
        b'<company id="B" href="https://x.org/b.xml" LastKnownDate="2026-06-01" KnownVersion="1"/>',
    )
    recorded["index"] = bumped

    assert cache.updates_available() is True


def test_cancel_stops_between_downloads(cache_env):
    cache, cache_dir, recorded = cache_env

    def cancel_after_first(done, total, name):
        return done >= 1  # truthy → abort

    report = cache.update_producers_data(on_progress=cancel_after_first)

    assert recorded["urls"] == ["https://x.org/a.xml"]  # second download never starts
    assert [p.name for p in report.updated] == ["a.xml"]
    assert (cache_dir / "Producers" / "b.xml").exists() is False
```

(The fixture stores the index bytes in `recorded["index"]` and `cache_env`'s fake_urlopen serves them; final test file must contain exactly these five tests: first refresh, second refresh, single-outdated, updates-available ×2 (empty-cache True + post-refresh False then remote-bump True), cancel.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender tests/test_cache.py -q`
Expected: FAIL — `TypeError: update_producers_data() got an unexpected keyword argument 'on_progress'` (and `AttributeError` for `updates_available`).

- [ ] **Step 3: Implement** — replace lines 61-110 of `src/materialsdb/cache.py` with:

```python
class _IndexPlan:
    def __init__(self, index, new_index, unchanged, todos):
        self.index = index
        self.new_index = new_index
        self.unchanged = unchanged
        self.todos = todos  # list[(company, cached_producer|None, producer_path)]


def _plan_index(index):
    cached_index = parse_cached_index(index)
    cached_root = cached_index.getroot()
    with urllib.request.urlopen(index) as response:
        new_index = etree.parse(response)
    new_root = new_index.getroot()
    producers_dir = get_producers_dir()
    unchanged = []
    todos = []
    for company in new_root:
        cached_producer = get_by_id(cached_root, company.get("id"))
        producer_path = producers_dir / pathlib.Path(company.get("href")).name
        if not require_update(cached_producer, company) and producer_path.exists():
            unchanged.append(producer_path)
            continue
        todos.append((company, cached_producer, producer_path))
    return _IndexPlan(index, new_index, unchanged, todos)


def update_producers_data(url_list=MATERIALSDBINDEXURLLIST, on_progress=None):
    """on_progress(done, total, name) is called after each producer download
    with a global running total; returning a truthy value stops the update
    between two downloads (the partial Report is returned as-is)."""
    plans = [_plan_index(index) for index in url_list]
    total = sum(len(plan.todos) for plan in plans)
    existing, updated, deleted = [], [], []
    done = 0
    for plan in plans:
        for company, cached_producer, producer_path in plan.todos:
            if cached_producer is not None:
                cached_path = get_producers_dir() / pathlib.Path(cached_producer.get("href")).name
                deleted.append(cached_path)
                cached_path.unlink(True)
            urllib.request.urlretrieve(company.get("href"), producer_path)
            updated.append(producer_path)
            done += 1
            if on_progress is not None and on_progress(
                done, total, pathlib.Path(company.get("href")).name
            ):
                return Report(existing, updated, deleted)
        if plan.todos:
            plan.new_index.write(str(get_cached_index_path(plan.index)))
        existing.extend(plan.unchanged)
    return Report(existing, updated, deleted)


def update_producers_from_index(index):
    """Legacy single-index entry point (no progress callback)."""
    return update_producers_data([index])


def updates_available(url_list=MATERIALSDBINDEXURLLIST) -> bool:
    """True when any remote company differs from the cached index."""
    for index in url_list:
        cached_root = parse_cached_index(index).getroot()
        with urllib.request.urlopen(index) as response:
            new_root = etree.parse(response).getroot()
        for company in new_root:
            if require_update(get_by_id(cached_root, company.get("id")), company):
                return True
    return False
```

(Note: `update_producers_from_index` delegating to `update_producers_data([index])` preserves the old single-index behaviour including the writers; check callers via `rg -n "update_producers_from_index" src tests bonsai_addon` — none outside cache.py, so the delegation is safe.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender tests/test_cache.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Full-file gates + commit**

```bash
ruff check --exclude src/materialsdb/classes.py src tests && ruff format --exclude "src/materialsdb/classes.py" src tests
ty check --project .
PYTHONPATH=src python3 -m pytest -p no:pytest-blender -q   # refresh tests must stay green pre-thread-ification
git add src/materialsdb/cache.py tests/test_cache.py
git commit -m "feat(cache): download plan with progress callback and updates_available"
```

---

### Task 2: server — background refresh job + updates flag

**Files:**
- Modify: `src/materialsdb/gui/server.py` (`GuiState` lines ~92-102; `_refresh` (current implementation at lines 639-666 — replaced); do_GET routing (line ~479 elif chain); do_POST routing (line ~483 elif chain); module-level `_refresh_worker`, `_progress_cb` helpers)
- Modify: `src/materialsdb/gui/__main__.py` (startup update-check thread in `main`)
- Test: `tests/test_gui_server.py` (REWRITE `test_refresh_downloads_then_uploads_cache_into_store` and `test_refresh_network_failure`; add new tests)

**Interfaces:**
- Consumes: Task 1 `cache.update_producers_data(url_list, on_progress)` (truthy callback cancels), `cache.updates_available() -> bool`.
- Produces: `GuiState.updates_available: bool`, `GuiState.refresh_job: dict | None` with keys `status` (`"running"`/`"complete"`/`"error"`/`"cancelled"`), `done`, `total`, `label`, `error`, `report` (the old 200 payload dict), `cancelled`. Endpoints: `POST /api/refresh` → `{"started": true}` immediately, `409 {"error": "already running"}` while running; `POST /api/refresh/cancel` → `{ok: true}` / `404`; `GET /api/refresh/status` → job snapshot (`{"status": "idle", …}` when never run); `GET /api/updates` → `{"updates_available": bool}` (GETs not token-gated).

- [ ] **Step 1: Rewrite the two existing refresh tests + add new ones** (edit `tests/test_gui_server.py`; replaces the current implementations of the two named tests, below `test_mutations_require_token`)

```python
def _wait_for_status(server, token, want, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, payload = request(server, "GET", "/api/refresh/status")
        assert status == 200
        if payload["status"] == want:
            return payload
        time.sleep(0.05)
    pytest.fail(f"refresh job never reached {want!r}: {payload}")


def test_refresh_job_completes_and_clears_updates(api, monkeypatch):
    from materialsdb import cache, query
    from materialsdb.store import Report as StoreReport

    progress_calls = []
    ingest_forces = []

    def fake_download(on_progress=None):
        if on_progress is not None:
            on_progress(1, 2, "a.xml")
            on_progress(2, 2, "b.xml")
        return cache.Report(existing=[], updated=["a.xml", "b.xml"], deleted=[])

    store_report = StoreReport(existing=[1], updated=[2], deleted=[], skipped=[], duplicates=[])

    def fake_ingest(force=False):
        ingest_forces.append(force)
        return store_report

    monkeypatch.setattr(cache, "update_producers_data", fake_download)
    monkeypatch.setattr(query, "refresh", fake_ingest)

    server, state = api
    state.updates_available = True
    status, _ = request(server, "POST", "/api/refresh", payload={}, token=state.token)
    assert status == 200

    payload = _wait_for_status(server, state.token, "complete")
    assert ingest_forces == [False]
    assert payload["report"]["downloaded"] == 2
    assert payload["report"]["updated"] == [str(p) for p in store_report.updated]
    assert state.updates_available is False  # successful refresh clears the flag


def test_refresh_network_failure_reports_error(api, monkeypatch):
    from materialsdb import cache, query

    ingested = []

    def boom(on_progress=None):
        raise OSError("offline")

    monkeypatch.setattr(cache, "update_producers_data", boom)
    monkeypatch.setattr(query, "refresh", lambda force=False: ingested.append(force) or None)

    server, state = api
    status, _ = request(server, "POST", "/api/refresh", payload={}, token=state.token)
    assert status == 200

    payload = _wait_for_status(server, state.token, "error")
    assert "cache update failed" in payload["error"]
    assert "offline" in payload["error"]
    assert ingested == []  # failure aborts before ingestion
    assert state.refresh_job["status"] == "error"


def test_refresh_second_start_while_running_conflicts(api, monkeypatch):
    from materialsdb import cache, query

    running = threading.Event()
    release = threading.Event()

    def slow_download(on_progress=None):
        running.set()
        release.wait(5.0)
        return cache.Report(existing=[], updated=[], deleted=[])

    monkeypatch.setattr(cache, "update_producers_data", slow_download)
    monkeypatch.setattr(query, "refresh", lambda force=False: None)

    server, state = api
    status, _ = request(server, "POST", "/api/refresh", payload={}, token=state.token)
    assert status == 200
    assert running.wait(5.0)

    status, payload = request(server, "POST", "/api/refresh", payload={}, token=state.token)
    assert status == 409
    assert payload["error"] == "already running"

    release.set()
    _wait_for_status(server, state.token, "complete")


def test_refresh_cancel_stops_before_ingest(api, monkeypatch):
    from materialsdb import cache, query

    ingested = []
    progress_calls = []

    def fake_download(on_progress=None):
        if on_progress:
            if on_progress(1, 2, "a.xml"):
                return cache.Report([], ["a.xml"], [])
            if on_progress(2, 2, "b.xml"):
                return cache.Report([], ["a.xml", "b.xml"], [])
        return cache.Report(existing=[], updated=["a.xml", "b.xml"], deleted=[])

    monkeypatch.setattr(cache, "update_producers_data", fake_download)
    monkeypatch.setattr(query, "refresh", lambda force=False: ingested.append(force) or None)

    server, state = api
    status, _ = request(server, "POST", "/api/refresh", payload={}, token=state.token)
    assert status == 200

    deadline = time.time() + 5.0
    cancelled = False
    while time.time() < deadline and not cancelled:
        time.sleep(0.05)
        job = state.refresh_job
        if job and job["done"] >= 1:
            status2, _ = request(server, "POST", "/api/refresh/cancel", payload={}, token=state.token)
            if status2 == 200:
                cancelled = True
    assert cancelled

    payload = _wait_for_status(server, state.token, "cancelled")
    assert ingested == []
    assert payload["done"] >= 1


def test_refresh_updates_endpoint_and_no_job(api, monkeypatch):
    server, state = api
    status, payload = request(server, "GET", "/api/updates")
    assert status == 200 and payload["updates_available"] is False

    state.updates_available = True
    status, payload = request(server, "GET", "/api/updates")
    assert payload["updates_available"] is True

    status, payload = request(server, "GET", "/api/refresh/status")
    assert status == 200 and payload["status"] == "idle"


def test_make_server_spawns_no_network_check(api, monkeypatch):
    from materialsdb import cache

    calls = []
    monkeypatch.setattr(cache, "updates_available", lambda: calls.append(1) or False)

    from materialsdb.gui.server import GuiState, make_server

    server = make_server(state=GuiState())
    server.shutdown()
    server.server_close()
    assert calls == []  # the update check belongs to __main__, never to make_server
```

(Remove the old implementations of `test_refresh_downloads_then_uploads_cache_into_store` and `test_refresh_network_failure_reports_error_without_ingesting` wholesale — the new tests above replace them. `threading`/`time` are already imported in the file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -q -k refresh`
Expected: FAIL — the old synchronous tests are gone; the new ones fail on "already running"-shape absence (`payload["status"] == "idle"` KeyError / 404 on the new endpoints).

- [ ] **Step 3: Implement server changes**

`GuiState.__init__` — add after `self.incoming = []`:

```python
        self.updates_available = False
        # status: running|complete|error|cancelled; report: old 200 payload
        self.refresh_job: dict | None = None
```

Module-level helpers (place near `_refresh`, before the handler class methods that use them):

```python
def _progress_cb(state):
    def on_progress(done, total, name):
        job = state.refresh_job
        job.update(done=done, total=total, label=f"downloading producer files {done}/{total} ({name})")
        return bool(job.get("cancelled"))
    return on_progress


def _refresh_worker(state, force):
    import urllib.error
    from materialsdb import cache, query

    state.refresh_job = {
        "status": "running", "done": 0, "total": 0, "label": "checking materialsdb.org",
        "error": None, "report": None, "cancelled": False,
    }
    try:
        cache.update_producers_data(on_progress=_progress_cb(state))
    except (OSError, urllib.error.URLError) as err:
        state.refresh_job.update(status="error", error=f"cache update failed: {err}")
        return
    if state.refresh_job.get("cancelled"):
        state.refresh_job.update(status="cancelled")
        return
    state.refresh_job.update(label="indexing…")
    try:
        report = query.refresh(force=force)
    except Exception as err:  # noqa: BLE001 - the job must never raise into the void
        state.refresh_job.update(status="error", error=str(err))
        return
    if state.refresh_job.get("cancelled"):
        state.refresh_job.update(status="cancelled")
        return
    try:
        state.updates_available = cache.updates_available()
    except Exception:  # noqa: BLE001/S110 - flag is advisory, keep the old one on failure
        pass
    if state.updates_available is False:
        state.refresh_job.update(status="complete", done=0, total=0, label="", cancelled=False, report={
            "existing": len(report.existing),
            "updated": [str(p) for p in report.updated],
            "deleted": len(report.deleted),
            "skipped": len(report.skipped),
            "duplicates": len(report.duplicates),
            "downloaded": state.refresh_job["downloaded"],
        })
```

Wait — `downloaded` is not tracked separately; derive it from the progress job before resetting: capture `downloaded = state.refresh_job["done"]` BEFORE the `updates_available` reset line, and set `"downloaded": downloaded` in the report. The implementer wires this correctly; the report contract (keys of the old 200 body) stays: existing (int), updated (list of str), deleted (int), skipped (int), duplicates (int), downloaded (int).

Handler replacements:

```python
    def _refresh(self, payload):
        job = self.state.refresh_job
        if job is not None and job["status"] == "running":
            self._send(409, {"error": "already running"})
            return
        force = bool(payload.get("force"))
        threading.Thread(
            target=_refresh_worker, args=(self.state, force), daemon=True
        ).start()
        self._send(200, {"started": True})

    def _refresh_cancel(self):
        job = self.state.refresh_job
        if job is None or job["status"] != "running":
            self._send(404, {"error": "no refresh running"})
            return
        job["cancelled"] = True
        self._send(200, {"ok": True})

    def _refresh_status(self):
        job = self.state.refresh_job
        if job is None:
            job = {"status": "idle", "done": 0, "total": 0, "label": "",
                   "error": None, "report": None, "cancelled": False}
        self._send(200, dict(job))
```

Routing: do_GET elif chain gains
```python
        if parsed.path == "/api/refresh/status":
            self._refresh_status()
        elif parsed.path == "/api/updates":
            self._send(200, {"updates_available": self.state.updates_available})
```
do_POST elif chain gains
```python
            elif parsed.path == "/api/refresh/cancel":
                self._refresh_cancel()
```
and `threading` import at the top of server.py if missing.

`__main__.py` `main()` — after `write_listener_info(...)` and before `print`:

```python
    threading.Thread(
        target=_startup_update_check, args=(server.gui_state,), daemon=True
    ).start()
```

with module-level:

```python
def _startup_update_check(state):
    from materialsdb import cache

    try:
        state.updates_available = cache.updates_available()
    except Exception:  # noqa: BLE001, S110 - advisory flag: quiet on network errors
        pass
```

(add `import threading`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src python3 -m pytest -p no:pytest-blender tests/test_gui_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
ruff check --exclude src/materialsdb/classes.py src tests && ruff format --exclude "src/materialsdb/classes.py" src tests
ty check --project .
git add src/materialsdb/gui/server.py src/materialsdb/gui/__main__.py tests/test_gui_server.py
git commit -m "feat(gui): refresh runs as a cancellable background job with status endpoint"
```

---

### Task 3: frontend — overlay, banners, docs

**Files:**
- Modify: `src/materialsdb/gui/static/index.html` (buttons row ~line 42; status paragraph ~line 55)
- Modify: `src/materialsdb/gui/static/app.js` (`loadMaterials`; refresh handler lines 414-417)
- Modify: `README.md` (GUI paragraph ~line 73 region)
- Test: `tests/test_construction_frontend.py` unaffected; use browser for visual verification manually later.

**Interfaces:**
- Consumes: Task 2 endpoints `POST /api/refresh` → started/409; `GET /api/refresh/status` → `{status, done, total, error, report}`; `POST /api/refresh/cancel` → `{ok}`/`404`; `GET /api/updates` → `{updates_available}`.
- Produces: DOM ids `#refresh-overlay`, `#hint`, `#updates` used only by app.js.

- [ ] **Step 1: index.html** — after `<button id="refresh">refresh cache</button>` add:

```html
  <span id="updates" style="display:none;color:#a33;background:#fff3f3;border:1px solid #a33;border-radius:3px;padding:.1rem .4rem">newer data available on materialsdb.org — refresh when convenient</span>
```

and after `<p id="status"></p>`:

```html
 <p id="hint" style="display:none;color:#888;margin:.2rem 0"></p>
```

- [ ] **Step 2: app.js — replace the refresh handler** (currently lines 414-417) with:

```js
$("refresh").onclick = runRefresh;

async function runRefresh() {
  const existing = document.getElementById("refresh-overlay");
  if (existing) return;
  const overlay = document.createElement("div");
  overlay.id = "refresh-overlay";
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:1000";
  overlay.innerHTML = `<div style="background:#fff;border-radius:6px;padding:1rem 1.2rem;min-width:22rem;display:flex;flex-direction:column;gap:.6rem">` +
    `<b>refreshing materialsdb cache</b>` +
    `<div style="height:.6rem;background:#eee;border-radius:.3rem;overflow:hidden"><div id="refresh-bar" style="height:100%;width:0;background:#468;border-radius:.3rem;transition:width .3s"></div></div>` +
    `<div style="display:flex;justify-content:space-between;align-items:center"><span id="refresh-label" style="color:#666">starting…</span>` +
    `<button id="refresh-cancel">cancel</button></div></div>`;
  document.body.appendChild(overlay);
  overlay.querySelector("#refresh-cancel").onclick = () =>
    api("/api/refresh/cancel", { method: "POST", body: "{}" }).catch(() => {});
  try {
    await api("/api/refresh", { method: "POST", body: "{}" });
  } catch (err) {
    overlay.remove();
    setStatus(err.message);
    return;
  }
  let job = null;
  for (let i = 0; i < 200; i++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    try {
      job = await api("/api/refresh/status");
    } catch {
      overlay.remove();
      setStatus("server unreachable while refreshing");
      return;
    }
    if (job.status !== "running" && job.status !== "idle") break;
    const pct = job.total ? `${Math.round((job.done / job.total) * 100)}%` : (job.status === "running" ? "" : "100%");
    const bar = overlay.querySelector("#refresh-bar");
    bar.style.width = job.total ? `${Math.max(4, Math.round((job.done / job.total) * 100))}%` : "4%";
    overlay.querySelector("#refresh-label").textContent = job.label || "…";
  }
  overlay.remove();
  if (job && job.status === "cancelled") {
    setStatus(`refresh cancelled (${job.done}/${job.total || "?"} downloaded)`);
  } else if (job && job.status === "error") {
    setStatus(job.error || "refresh failed");
  } else if (job && job.report) {
    const r = job.report;
    setStatus(`cache refreshed: ${r.downloaded} downloaded, ${r.existing} unchanged, ${r.updated.length} indexed`);
  } else {
    setStatus("refresh interrupted");
  }
  await loadMaterials();
}
```

- [ ] **Step 3: app.js — notices** — inside `loadMaterials`, right after the `const { materials } = await api(...)` line, add:

```js
  const hint = document.getElementById("hint");
  if (hint) {
    hint.style.display = materials.length ? "none" : "block";
    hint.textContent = "No materials cached yet — click Refresh to download from materialsdb.org";
  }
```

and add a helper near `loadMaterials`:

```js
async function syncUpdatesBanner() {
  const banner = document.getElementById("updates");
  if (!banner) return;
  try {
    const { updates_available } = await api("/api/updates");
    banner.style.display = updates_available ? "inline" : "none";
  } catch {}
}
```

Call `syncUpdatesBanner()` where `loadMaterials()` is called at bootstrap and at the end of `runRefresh` (both call sites — grep `loadMaterials()`; the calls at lines 431/437 are bootstrap and refresh-related today, so simply append `await syncUpdatesBanner();` after each, and once inside `runRefresh` after `await loadMaterials();`).

- [ ] **Step 4: Verify frontend loads clean (syntax) then full gates**

```bash
node --input-type=module -e "import('/dev/stdin')" < /dev/null  2>/dev/null || true
node -e "new Function(require('fs').readFileSync('src/materialsdb/gui/static/app.js','utf8'))" && echo JS-OK
PYTHONPATH=src python3 -m pytest -p no:pytest-blender -q    # whole suite stays green (157-158 passed + new)
ruff check --exclude src/materialsdb/classes.py src tests bonsai_addon && ruff format --check . && ty check --project .
```

- [ ] **Step 5: README** — replace the paragraph sentence ending "...the empty list after one click." with:

```
*Refresh cache* downloads any newer producer files from materialsdb.org and
re-ingests them — on a fresh machine it fills the empty list after one click,
behind a progress dialog (cancellable). The app shows a notice when updates
are available on materialsdb.org.
```

- [ ] **Step 6: Commit**

```bash
git add src/materialsdb/gui/static/index.html src/materialsdb/gui/static/app.js README.md
git commit -m "feat(gui): refresh progress overlay with cancel; empty-list and update notices"
```

---

## Task 4: Browser verification (human)

Controller asks the HUMAN to verify in a real browser after Tasks 1-3:
```bash
XDG_CACHE_HOME=/tmp/fresh-md PYTHONPATH=src python3 -m materialsdb.gui --no-browser
```
Then in the GUI: empty-list notice appears; click refresh — overlay with busy bar and label, progress advancing by download counts (fake request unless server data real); Cancel halfway leaves partially downloaded files and reports `refresh cancelled (x/y downloaded)`; banner appears only when server-side check says data is stale.
Expected result: no JS console errors, list populated after refresh, banners toggle correctly.
```

## Self-Review

- Spec coverage: cache progress callback + flat plan + `updates_available` (Task 1); server job/409/cancel/status/api-updates/startup-check-in-`__main__`-only (Task 2); overlay+cancel banner, empty-list hint, update banner, README (Task 3); browser verification (Task 4 human step).
- Placeholders: none (Task 1 Step 1 self-corrects its own scaffolding snippet in-line with the final five tests explicitly enumerated).
- Type consistency: `on_progress(done, total, name) -> bool(cancel)` used identically across cache worker and server `_progress_cb`; `refresh_job` dict keys identical in `_refresh_worker`, `_refresh_status`, and tests.
