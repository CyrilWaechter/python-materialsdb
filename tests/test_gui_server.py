import http.client
import io
import json
import os
import socket
import struct
import threading
import time
from unittest import mock

import pytest

pytest.importorskip("ifcopenshell")  # export/session tests in Task 3 need it; harmless here


@pytest.fixture(autouse=True)
def pinned_fr_ch_config(monkeypatch, tmp_path):
    from materialsdb import config

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: config_dir)
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")
    recorded = {}
    # the config dir is redirected to tmp_path, so the real set_param is safe
    # to call: preference round-trips (scheme, ignore_producer_color) go to
    # disk while test_config_roundtrip still reads the recorded calls
    real_set_param = config.set_param

    def recording_set_param(param, value):
        recorded[param] = value
        real_set_param(param, value)

    monkeypatch.setattr("materialsdb.config.set_param", recording_set_param)
    pytest.config_recorded = recorded  # ty: ignore[unresolved-attribute] - dynamic test-global, read in test_config_roundtrip


def request(server, method, path, payload=None, token=None, want_headers=False):
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
        parsed = json.loads(data)
    else:
        parsed = data
    if want_headers:
        return response.status, parsed, dict(response.getheaders())
    return response.status, parsed


@pytest.fixture
def api(tmp_path, mini_xml, mixed_xml):
    from materialsdb.gui.server import GuiState, make_server
    from materialsdb.store import MaterialStore

    # one combined refresh: sequential refresh calls would drop mini (refresh
    # deletes sources absent from the current paths list)
    store_ = MaterialStore(db_path=tmp_path / "gui.db")
    store_.refresh(paths=[mini_xml, mixed_xml])
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


def test_app_js_served(api):
    server, _ = api
    status, body = request(server, "GET", "/app.js")
    assert status == 200
    assert b"MATERIALSDB_TOKEN" in body


def test_constructions_page_served(api):
    server, _ = api
    status, body = request(server, "GET", "/constructions.html")
    assert status == 200
    assert b"MATERIALSDB_TOKEN" in body
    status, body = request(server, "GET", "/app-constructions.js")
    assert status == 200


def test_materials_list_and_filters(api):
    server, _ = api
    status, payload = request(server, "GET", "/api/materials")
    assert status == 200
    ids = {m["id"][-3:] for m in payload["materials"]}
    assert ids == {"001", "002", "003", "004", "005"}
    first = payload["materials"][0]
    assert {"id", "company", "category", "type", "names", "lambda_min"} <= set(first)

    status, payload = request(server, "GET", "/api/materials?category=Insulation")
    assert [m["id"][-3:] for m in payload["materials"]] == ["001", "004"]

    status, payload = request(server, "GET", "/api/materials?type=btk&store_probe=1")
    assert [m["id"][-3:] for m in payload["materials"]] == ["004"]

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
    # designusage is not declared in materialsdb103.xsd, so the generated
    # Construction class drops it and the payload degrades to "".
    assert payload["designusage"] == "consDesignForWall"

    status, payload = request(server, "GET", "/api/materials/unknown")
    assert status == 404


def test_mutations_require_token(api):
    server, _ = api
    status, _ = request(server, "POST", "/api/refresh", payload={})
    assert status == 403

    status, _ = request(server, "POST", "/api/refresh", payload={}, token="wrong")
    assert status == 403


def _wait_for_status(server, token, want, timeout=10.0):
    deadline = time.time() + timeout
    payload = None
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


def test_refresh_unexpected_download_error_ends_in_error_not_wedged(api, monkeypatch):
    """A non-network exception (e.g. corrupt cached index -> XMLSyntaxError)
    must still transition the job to "error": a wedged "running" job would
    make every subsequent refresh 409 forever."""
    from materialsdb import cache, query

    ingested = []

    def corrupt(on_progress=None):
        raise ValueError("corrupt")

    monkeypatch.setattr(cache, "update_producers_data", corrupt)
    monkeypatch.setattr(query, "refresh", lambda force=False: ingested.append(force) or None)

    server, state = api
    status, _ = request(server, "POST", "/api/refresh", payload={}, token=state.token)
    assert status == 200

    payload = _wait_for_status(server, state.token, "error")
    assert "unexpected error" in payload["error"]
    assert "corrupt" in payload["error"]
    assert ingested == []
    # a second status poll confirms the job did not stick in "running"
    status, payload = request(server, "GET", "/api/refresh/status")
    assert status == 200 and payload["status"] == "error"
    # and a new refresh can be started (not 409)
    status, payload = request(server, "POST", "/api/refresh", payload={}, token=state.token)
    assert status == 200 and payload == {"started": True}


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

    def fake_download(on_progress=None):
        if on_progress:
            if on_progress(1, 2, "a.xml"):
                return cache.Report([], ["a.xml"], [])
            # the fake download is instant otherwise; hold mid-download
            # (bounded) until the cancel request lands so the race is gone
            deadline = time.time() + 5.0
            while time.time() < deadline and not on_progress(1, 2, "a.xml"):
                time.sleep(0.05)
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
    # serve_forever() was never started, so shutdown() would block forever;
    # closing the socket is all the cleanup needed here
    server.server_close()
    assert calls == []  # the update check belongs to __main__, never to make_server


def test_export_multi_material_roundtrip(api, tmp_path):
    import ifcopenshell

    server, state = api
    status, body = request(
        server,
        "POST",
        "/api/export",
        payload={
            "items": [{"id": "00000000-0000-0000-0000-000000000001"}, {"id": "00000000-0000-0000-0000-000000000002"}]
        },
        token=state.token,
    )
    assert status == 200
    out = tmp_path / "export.ifc"
    out.write_bytes(body)
    reopened = ifcopenshell.open(str(out))
    names = sorted(m.Name for m in reopened.by_type("IfcMaterial"))
    assert names == ["Beton B", "Isolant A", "Isolant A"]


def test_export_requires_items(api):
    server, state = api
    status, _ = request(server, "POST", "/api/export", payload={"items": []}, token=state.token)
    assert status == 400


def test_session_open_pick_save_flow(api, tmp_path):
    import ifcopenshell

    from materialsdb.ifc.material_builder import create_material_file

    # a target file to append into: standalone file for material 2
    _, state = api
    store_ = state.resolve_store()

    target = create_material_file("00000000-0000-0000-0000-000000000002", store_=store_)
    target_path = tmp_path / "project.ifc"
    target.write(str(target_path))

    server, state = api
    token = state.token

    status, payload = request(server, "POST", "/api/session/open", payload={"path": str(target_path)}, token=token)
    assert status == 200

    status, payload = request(
        server,
        "POST",
        "/api/pick",
        payload={"items": [{"id": "00000000-0000-0000-0000-000000000001"}]},
        token=token,
    )
    assert status == 200 and payload["added"] == 1

    save_as = tmp_path / "project-plus.ifc"
    status, payload = request(server, "POST", "/api/session/save", payload={"path": str(save_as)}, token=token)
    assert status == 200

    reopened = ifcopenshell.open(str(save_as))
    assert {m.Name for m in reopened.by_type("IfcMaterial")} >= {"Isolant A", "Beton B"}


def test_pick_with_layer_subset(api, tmp_path):
    import ifcopenshell

    server, state = api
    from materialsdb.ifc.material_builder import create_material_file

    target = create_material_file("00000000-0000-0000-0000-000000000002", store_=state.resolve_store())
    target_path = tmp_path / "p.ifc"
    target.write(str(target_path))
    await_open = request(server, "POST", "/api/session/open", payload={"path": str(target_path)}, token=state.token)
    assert await_open[0] == 200

    status, payload = request(
        server,
        "POST",
        "/api/pick",
        payload={
            "items": [
                {"id": "00000000-0000-0000-0000-000000000001", "layer_ids": ["00000000-0000-0000-0000-0000000000a2"]}
            ]
        },
        token=state.token,
    )
    assert status == 200 and payload["added"] == 1

    save_as = tmp_path / "subset.ifc"
    request(server, "POST", "/api/session/save", payload={"path": str(save_as)}, token=state.token)
    reopened = ifcopenshell.open(str(save_as))
    descriptions = {layer.Description for layer in reopened.by_type("IfcMaterialLayer")}
    assert "00000000-0000-0000-0000-0000000000a2" in descriptions
    assert "00000000-0000-0000-0000-0000000000a1" not in descriptions


def test_pick_without_session_conflicts(api):
    server, _ = api
    # fresh state without an open session
    _, fresh = api
    fresh.file = None
    fresh.session_path = None
    status, _ = request(
        server,
        "POST",
        "/api/pick",
        payload={"items": [{"id": "00000000-0000-0000-0000-000000000001"}]},
        token=fresh.token,
    )
    assert status == 409


def test_session_open_rejects_bad_path(api, tmp_path):
    server, state = api
    status, _ = request(
        server, "POST", "/api/session/open", payload={"path": str(tmp_path / "nope.ifc")}, token=state.token
    )
    assert status == 400


def test_pick_malformed_json_returns_400(api):
    server, state = api
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    body = b"{not json"
    conn.request(
        "POST",
        "/api/pick",
        body=body,
        headers={"Content-Length": str(len(body)), "X-MaterialsDB-Token": state.token},
    )
    response = conn.getresponse()
    payload = json.loads(response.read())
    conn.close()
    assert response.status == 400
    assert payload["error"].startswith("invalid json")


def test_config_roundtrip(api):
    recorded = pytest.config_recorded  # ty: ignore[unresolved-attribute] - set by pinned_fr_ch_config fixture

    status, _ = request(api[0], "POST", "/api/config", payload={"lang": "en", "country": "FR"}, token=api[1].token)

    assert status == 200
    assert recorded == {"lang": "en", "country": "FR"}


def test_client_disconnect_mid_response_is_swallowed(api):
    """A client that aborts before the response is written (tab close,
    aborted fetch) must not crash the handler thread with BrokenPipeError
    nor print a socketserver traceback."""
    server, state = api
    body = json.dumps({"country": "CH"}).encode()
    request_bytes = (
        b"POST /api/config HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + f"X-MaterialsDB-Token: {state.token}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )
    err = io.StringIO()
    with mock.patch("sys.stderr", err):
        sock = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5)
        sock.sendall(request_bytes)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()  # send RST: server's response write hits a dead pipe
        time.sleep(0.3)
    assert "Traceback" not in err.getvalue()
    assert "Exception occurred" not in err.getvalue()
    # server stays healthy afterwards
    assert request(server, "GET", "/")[0] == 200


def test_materials_rows_carry_display_name(api):
    server, _ = api
    _, payload = request(server, "GET", "/api/materials?category=Insulation")
    row = payload["materials"][0]
    assert row["display_name"] == "Isolant A"

    _, payload = request(server, "GET", "/api/materials?lang=de&category=Insulation")
    assert payload["materials"][0]["display_name"] == "Daemmstoff A"


def test_detail_layers_array(api):
    server, _ = api
    status, payload = request(server, "GET", "/api/materials/00000000-0000-0000-0000-000000000001")
    assert status == 200
    assert [layer["thick"] for layer in payload["layers"]] == [200, 100]
    assert payload["layers"][0]["id"] == "00000000-0000-0000-0000-0000000000a1"
    assert payload["layers"][0]["lambda_value"] == 0.036


def test_detail_type_specific_arrays(api):
    server, _ = api

    _, payload = request(server, "GET", "/api/materials/00000000-0000-0000-0000-000000000004")
    assert payload["type"] == "btk"
    assert payload["layers"] == []
    assert [(v["thick"], v["u_value_without"]) for v in payload["variations"]] == [(200, 0.25), (300, 0.18)]

    _, payload = request(server, "GET", "/api/materials/00000000-0000-0000-0000-000000000005")
    assert payload["variations"] == []


def test_non_dict_item_rejected(api):
    server, state = api
    status, payload = request(
        server,
        "POST",
        "/api/export",
        payload={"items": ["00000000-0000-0000-0000-000000000001"]},
        token=state.token,
    )
    assert status == 400
    assert "invalid item" in payload["error"]


@pytest.fixture
def constructions_api(api, tmp_path, monkeypatch):
    import materialsdb.construction as cm

    server, state = api
    monkeypatch.setattr(cm, "constructions_dir", lambda: tmp_path / "constr")
    return server, state


def test_construction_crud_endpoints(constructions_api):
    server, state = constructions_api
    token = state.token
    body = {
        "name": "My wall",
        "design_usage": "consDesignForWall",
        "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}],
    }

    from urllib.parse import quote

    status, payload = request(server, "POST", f"/api/constructions/{quote('My wall')}", payload=body, token=token)
    assert status == 200

    status, payload = request(server, "GET", "/api/constructions")
    assert payload["constructions"] == ["My wall"]

    status, payload = request(server, "GET", f"/api/constructions/{quote('My wall')}")
    assert payload["name"] == "My wall"

    status, payload = request(
        server, "POST", "/api/u_value", payload={"construction": body, "preset": "ISO6946"}, token=token
    )
    assert status == 200 and payload["u"] > 0

    status, payload = request(server, "DELETE", f"/api/constructions/{quote('My wall')}", token=token)
    assert status == 200


def test_construction_validation_error_surfaces(constructions_api):
    server, state = constructions_api
    status, payload = request(
        server,
        "POST",
        "/api/constructions/bad",
        payload={"name": "bad", "layers": [{"material_id": "nope", "thickness_m": 0.1}]},
        token=state.token,
    )
    assert status == 400
    assert "nope" in payload["error"]


def _bare_session_file(path):
    import uuid

    from materialsdb import utils
    from materialsdb.ifc.project_library import ProjectLibrary

    library = ProjectLibrary()
    library.create_project_library(company="Session", companyid=str(uuid.uuid4()), ver=1, crd=utils.new_tdatetime())
    library.file.write(str(path))


def test_export_construction_endpoint(api, tmp_path):
    import ifcopenshell

    server, state = api
    status, body, headers = request(
        server,
        "POST",
        "/api/export-construction",
        payload={
            "construction": {
                "name": "rev-wall",
                "design_usage": None,
                "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}],
            }
        },
        token=state.token,
        want_headers=True,
    )
    assert status == 200
    assert "filename=" in headers.get("Content-Disposition", "")
    out = tmp_path / "rev-wall.ifc"
    out.write_bytes(body)
    reopened = ifcopenshell.open(str(out))
    sets = reopened.by_type("IfcMaterialLayerSet")
    assert len(sets) == 1
    assert sets[0].LayerSetName == "rev-wall"
    assert len(reopened.by_type("IfcMaterial")) == 1
    layers = reopened.by_type("IfcMaterialLayer")
    assert len(layers) == 1
    assert layers[0].LayerThickness == 0.15


def test_append_construction_endpoint(api, tmp_path):
    import ifcopenshell

    from materialsdb.ifc.material_builder import create_material_file

    server, state = api
    store_ = state.resolve_store()
    target = create_material_file("00000000-0000-0000-0000-000000000002", store_=store_)
    target_path = tmp_path / "session.ifc"
    target.write(str(target_path))
    assert request(server, "POST", "/api/session/open", payload={"path": str(target_path)}, token=state.token)[0] == 200

    construction = {
        "name": "double",
        "design_usage": None,
        "layers": [
            {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2},
        ],
    }
    status, payload = request(
        server, "POST", "/api/append-construction", payload={"construction": construction}, token=state.token
    )
    assert status == 200 and payload["layer_count"] == 2

    save_as = tmp_path / "session-plus.ifc"
    assert request(server, "POST", "/api/session/save", payload={"path": str(save_as)}, token=state.token)[0] == 200
    reopened = ifcopenshell.open(str(save_as))
    descriptions = {layer.Description for layer in reopened.by_type("IfcMaterialLayer")}
    assert "00000000-0000-0000-0000-000000000001" in descriptions
    assert "00000000-0000-0000-0000-000000000002" in descriptions
    assert len([s for s in reopened.by_type("IfcMaterialLayerSet") if s.LayerSetName == "double"]) == 1


def test_append_construction_requires_session(api):
    server, state = api
    state.file = None
    status, payload = request(
        server,
        "POST",
        "/api/append-construction",
        payload={"construction": {"name": "x", "layers": [{"material_id": "nope", "thickness_m": 0.1}]}},
        token=state.token,
    )
    assert status == 409
    assert payload["error"] == "no session open"


def test_append_construction_twice_yields_single_set(api, tmp_path):
    import ifcopenshell

    from materialsdb.ifc.material_builder import MATERIALSDB_PSET

    server, state = api
    session = tmp_path / "session.ifc"
    _bare_session_file(session)
    assert request(server, "POST", "/api/session/open", payload={"path": str(session)}, token=state.token)[0] == 200

    construction = {
        "name": "twice",
        "design_usage": None,
        "layers": [
            {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2},
        ],
    }
    for _ in range(2):
        status, payload = request(
            server, "POST", "/api/append-construction", payload={"construction": construction}, token=state.token
        )
        assert status == 200 and payload["layer_count"] == 2

    save_as = tmp_path / "session-twice.ifc"
    assert request(server, "POST", "/api/session/save", payload={"path": str(save_as)}, token=state.token)[0] == 200
    reopened = ifcopenshell.open(str(save_as))
    sets = [s for s in reopened.by_type("IfcMaterialLayerSet") if s.LayerSetName == "twice"]
    assert len(sets) == 1
    assert len(sets[0].MaterialLayers) == 2
    assert len(reopened.by_type("IfcMaterial")) == 2
    psets = [p for p in reopened.by_type("IfcMaterialProperties") if p.Name == MATERIALSDB_PSET]
    assert len(psets) == 2


def test_legacy_construction_endpoint(api):
    server, _state = api
    status, payload = request(
        server,
        "GET",
        "/api/constructions/legacy/00000000-0000-0000-0000-000000000005",
    )
    assert status == 200
    assert payload["consref"] == "REF-E"
    variant = payload["variants"][0]
    assert variant["layers"][0]["thickness_m"] == 0.2
    assert status == 200

    status, payload = request(server, "GET", "/api/constructions/legacy/unknown")
    assert status == 404


def test_listener_register_poll_send_roundtrip(api):
    server, state = api
    status, _ = request(
        server,
        "POST",
        "/api/listener/register",
        payload={"client_id": "c1", "model_path": "/tmp/model.ifc"},
        token=state.token,
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
        server,
        "POST",
        "/api/listener/send",
        payload={
            "client_id": "c1",
            "action": "add_materials",
            "items": [{"id": "00000000-0000-0000-0000-000000000001"}],
        },
        token=state.token,
    )
    assert status == 200
    assert body["queued"] == 2  # Isolant A has 2 layers -> 2 IfcMaterial entries
    assert body["missing"] == []

    status, body = request(server, "GET", "/api/listener/poll?client_id=c1", token=state.token)
    assert status == 200
    assert body["payload"]["action"] == "add_materials"

    status, body = request(server, "GET", "/api/listener/poll?client_id=c1", token=state.token)
    assert status == 204  # cleared


def test_listener_model_map_ingested(api, tmp_path):
    server, state = api
    model_path = str(tmp_path / "m.ifc")
    payload = {
        "client_id": "c1",
        "model_path": model_path,
        "scheme": "materialsdb-fp/1",
        "materials": {"ID1": {"fingerprint": "f", "scheme": "materialsdb-fp/1", "layers": []}},
    }
    status, body = request(server, "POST", "/api/listener/model", payload=payload, token=state.token)
    assert status == 200
    assert body == {"ok": True}
    assert state.resolve_store().get_model_materials(model_path)["ID1"]["fingerprint"] == "f"


def test_listener_model_requires_token(api, tmp_path):
    server, _state = api
    status, _ = request(
        server,
        "POST",
        "/api/listener/model",
        payload={"client_id": "c1", "model_path": str(tmp_path / "m.ifc"), "materials": {}},
    )
    assert status == 403


def test_listener_send_records_snapshot(api):
    server, state = api
    model_path = "/tmp/snapshot.ifc"
    request(
        server,
        "POST",
        "/api/listener/register",
        payload={"client_id": "c1", "model_path": model_path},
        token=state.token,
    )

    status, _ = request(
        server,
        "POST",
        "/api/listener/send",
        payload={
            "client_id": "c1",
            "action": "add_materials",
            "items": [{"id": "00000000-0000-0000-0000-000000000001"}],
        },
        token=state.token,
    )
    assert status == 200
    snapshot = state.resolve_store().get_pushed_material(
        model_path, "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-0000000000a1"
    )
    assert snapshot is not None
    assert snapshot["identity"]["material_id"] == "00000000-0000-0000-0000-000000000001"


def test_listener_send_construction_records_snapshot(api):
    server, state = api
    model_path = "/tmp/snapshot-construction.ifc"
    request(
        server,
        "POST",
        "/api/listener/register",
        payload={"client_id": "c1", "model_path": model_path},
        token=state.token,
    )

    status, _ = request(
        server,
        "POST",
        "/api/listener/send",
        payload={
            "client_id": "c1",
            "action": "add_construction",
            "construction": {
                "name": "snap wall",
                "design_usage": "consDesignForWall",
                "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}],
            },
        },
        token=state.token,
    )
    assert status == 200
    snapshot = state.resolve_store().get_pushed_material(model_path, M2, L2B)
    assert snapshot is not None
    assert snapshot["identity"]["material_id"] == M2


def test_listener_send_construction_snapshot_produces_change_report(api):
    """The construction snapshot must be keyed by the entry's org_layer layer
    id, or /api/model/changes reports has_snapshot=False for construction
    materials (Important 2)."""
    server, state = api
    store_ = state.resolve_store()
    model_path = "/tmp/snapshot-construction-report.ifc"
    request(
        server,
        "POST",
        "/api/listener/register",
        payload={"client_id": "c1", "model_path": model_path},
        token=state.token,
    )

    status, _ = request(
        server,
        "POST",
        "/api/listener/send",
        payload={
            "client_id": "c1",
            "action": "add_construction",
            "construction": {
                "name": "snap wall",
                "design_usage": "consDesignForWall",
                "layers": [{"material_id": M2, "thickness_m": 0.15}],
            },
        },
        token=state.token,
    )
    assert status == 200

    snapshot = store_.get_pushed_material(model_path, M2, L2B)
    assert snapshot is not None

    # model map says the pushed material is stale -> `changed`
    _ingest_model(
        server,
        state,
        model_path,
        {M2: {"fingerprint": "stale", "scheme": "materialsdb-fp/1", "layers": [{"layer_id": L2B, "thick": 0.15}]}},
        register=False,
    )
    status, body = request(server, "GET", "/api/model/changes")
    assert status == 200
    item = body["changed"][0]
    assert item["material_id"] == M2
    assert item["has_snapshot"] is True


def test_listener_send_unknown_client_404(api):
    server, state = api
    status, body = request(
        server,
        "POST",
        "/api/listener/send",
        payload={"client_id": "ghost", "action": "add_materials", "items": [{"id": "x"}]},
        token=state.token,
    )
    assert status == 404
    assert "ghost" in body["error"]


def test_listener_requires_token(api):
    server, _state = api
    assert request(server, "POST", "/api/listener/register", payload={"client_id": "c"})[0] == 403
    assert request(server, "GET", "/api/listener/poll?client_id=c")[0] == 403


def test_listener_status_and_clients(api):
    server, state = api
    request(
        server,
        "POST",
        "/api/listener/register",
        payload={"client_id": "c1", "model_path": "/tmp/m.ifc"},
        token=state.token,
    )
    status, _ = request(
        server,
        "POST",
        "/api/listener/status",
        payload={"client_id": "c1", "status": "applied", "detail": "2 material(s)"},
        token=state.token,
    )
    assert status == 200
    status, body = request(server, "GET", "/api/listener/clients", token=state.token)
    assert status == 200
    assert body["clients"] == [
        {"client_id": "c1", "model_path": "/tmp/m.ifc", "last_status": {"status": "applied", "detail": "2 material(s)"}}
    ]


def test_listener_send_resets_status_to_pending(api):
    """A fresh push invalidates the previous applied/error mark so the GUI's
    target dropdown shows a pending state until the new result arrives."""
    server, state = api
    request(server, "POST", "/api/listener/register", payload={"client_id": "c1", "model_path": ""}, token=state.token)
    request(
        server,
        "POST",
        "/api/listener/status",
        payload={"client_id": "c1", "status": "applied", "detail": "old"},
        token=state.token,
    )

    status, _ = request(
        server,
        "POST",
        "/api/listener/send",
        payload={
            "client_id": "c1",
            "action": "add_materials",
            "items": [{"id": "00000000-0000-0000-0000-000000000001"}],
        },
        token=state.token,
    )
    assert status == 200

    status, body = request(server, "GET", "/api/listener/clients", token=state.token)
    assert status == 200
    assert body["clients"][0]["last_status"] == {"status": "pending", "detail": ""}


def test_listener_stale_client_pruned(api, monkeypatch):
    server, state = api
    request(server, "POST", "/api/listener/register", payload={"client_id": "old", "model_path": ""}, token=state.token)
    listener = state.listeners["old"]
    listener["last_seen"] -= 10  # older than the 5s threshold
    status, body = request(server, "GET", "/api/listener/clients", token=state.token)
    assert status == 200
    assert body["clients"] == []
    assert "old" not in state.listeners


def test_listener_send_add_construction(api):
    server, state = api
    status, _ = request(
        server,
        "POST",
        "/api/listener/register",
        payload={"client_id": "c1", "model_path": "/tmp/m.ifc"},
        token=state.token,
    )
    assert status == 200

    status, body = request(
        server,
        "POST",
        "/api/listener/send",
        payload={
            "client_id": "c1",
            "action": "add_construction",
            "construction": {
                "name": "Mur 20+16",
                "design_usage": "consDesignForWall",
                "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}],
            },
        },
        token=state.token,
    )
    assert status == 200
    assert body["queued"] == 1  # layers count
    assert "Mur 20+16" in body["summary"]

    status, body = request(server, "GET", "/api/listener/poll?client_id=c1", token=state.token)
    assert status == 200
    assert body["payload"]["action"] == "add_construction"
    assert body["payload"]["construction"]["types"] == ["IfcWallType"]


def test_listener_send_add_construction_surfaces_warnings(api):
    """Important 2: a construction whose design thickness matches no layer
    yields a resolver warning that must reach the client, not just the summary."""
    server, state = api
    request(server, "POST", "/api/listener/register", payload={"client_id": "c1", "model_path": ""}, token=state.token)

    status, body = request(
        server,
        "POST",
        "/api/listener/send",
        payload={
            "client_id": "c1",
            "action": "add_construction",
            "construction": {
                "name": "Warn wall",
                "design_usage": "consDesignForWall",
                # Isolant A has 200 mm and 100 mm layers; 0.15 m matches neither
                "layers": [{"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.15}],
            },
        },
        token=state.token,
    )

    assert status == 200
    assert body["warnings"] == ["Isolant A: design thickness 150 mm matches no layer"]

    status, body = request(server, "GET", "/api/listener/poll?client_id=c1", token=state.token)
    assert body["payload"]["construction"]["warnings"] == ["Isolant A: design thickness 150 mm matches no layer"]


def test_listener_send_add_materials_has_empty_warnings(api):
    server, state = api
    request(server, "POST", "/api/listener/register", payload={"client_id": "c1", "model_path": ""}, token=state.token)

    status, body = request(
        server,
        "POST",
        "/api/listener/send",
        payload={
            "client_id": "c1",
            "action": "add_materials",
            "items": [{"id": "00000000-0000-0000-0000-000000000001"}],
        },
        token=state.token,
    )

    assert status == 200
    assert body["warnings"] == []


def test_listener_send_add_construction_invalid(api):
    server, state = api
    request(server, "POST", "/api/listener/register", payload={"client_id": "c1", "model_path": ""}, token=state.token)
    status, body = request(
        server,
        "POST",
        "/api/listener/send",
        payload={"client_id": "c1", "action": "add_construction", "construction": {"name": ""}},
        token=state.token,
    )
    assert status == 400
    assert "name required" in body["error"]


# ---------------------------------------------------------------------------
# Task 5: model status, layer matching and /api/model/changes
# ---------------------------------------------------------------------------

M1 = "00000000-0000-0000-0000-000000000001"
M2 = "00000000-0000-0000-0000-000000000002"
M4 = "00000000-0000-0000-0000-000000000004"
L1A = "00000000-0000-0000-0000-0000000000a1"
L1B = "00000000-0000-0000-0000-0000000000a2"
L2B = "00000000-0000-0000-0000-0000000000b1"

_AMBIG_ID = "00000000-0000-0000-0000-0000000000f1"
_AMBIG_LAYER_A = "00000000-0000-0000-0000-0000000000f2"
_AMBIG_LAYER_B = "00000000-0000-0000-0000-0000000000f3"

# Two layers of identical thickness, so a thickness match is ambiguous.
_AMBIGUOUS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<materials company="Ambig SA" companyid="C3D85A67-5B1E-4960-A297-2DE8275049C7" ver="1" crd="43979.7189866898" verXML="3" xmlns="http://www.materialsdb.org">
  <material id="00000000-0000-0000-0000-0000000000f1" readonly="1" type="simple">
    <information group="Concrete">
      <names><name lang="fr">Ambig F</name></names>
    </information>
    <layers>
      <layer id="00000000-0000-0000-0000-0000000000f2">
        <geometry country="CH" thick="100"/>
        <thermal country="CH" lambda_value="0.1"/>
      </layer>
      <layer id="00000000-0000-0000-0000-0000000000f3">
        <geometry country="CH" thick="100"/>
        <thermal country="CH" lambda_value="0.2"/>
      </layer>
    </layers>
  </material>
</materials>
"""


def _ingest_model(server, state, model_path, materials, client_id="c1", register=True):
    if register:
        status, _ = request(
            server,
            "POST",
            "/api/listener/register",
            payload={"client_id": client_id, "model_path": model_path},
            token=state.token,
        )
        assert status == 200
    status, body = request(
        server,
        "POST",
        "/api/listener/model",
        payload={
            "client_id": client_id,
            "model_path": model_path,
            "scheme": "materialsdb-fp/1",
            "materials": materials,
        },
        token=state.token,
    )
    assert status == 200 and body == {"ok": True}


def test_active_model_path_uses_most_recent_listener(api):
    from materialsdb.gui.server import _active_model_path

    _server, state = api
    assert _active_model_path(state) is None
    state.listeners["a"] = {"model_path": "/m/a.ifc", "last_seen": 10.0}
    state.listeners["b"] = {"model_path": "/m/b.ifc", "last_seen": 20.0}
    state.listeners["c"] = {"model_path": "", "last_seen": 30.0}
    assert _active_model_path(state) == "/m/b.ifc"


def test_material_status_matrix(api):
    from materialsdb.gui.server import _material_status

    _server, state = api
    store_ = state.resolve_store()
    path = "/m/status.ifc"

    store_.set_model_materials(
        path,
        {
            M1: {"fingerprint": "stale", "scheme": "materialsdb-fp/1", "layers": [{"layer_id": L1A, "thick": 0.2}]},
            M2: {
                "fingerprint": store_.material_fingerprint(M2),
                "scheme": "materialsdb-fp/1",
                "layers": [{"layer_id": L2B, "thick": 0.15}],
            },
            "ghost": {"fingerprint": "ffff", "scheme": "materialsdb-fp/1", "layers": [{"layer_id": "g", "thick": 0.3}]},
        },
    )
    assert _material_status(store_, path, M1) == "changed"
    assert _material_status(store_, path, M2) == "current"
    assert _material_status(store_, path, "ghost") == "gone"
    assert _material_status(store_, path, "missing") == "absent"

    store_.set_model_materials(
        path, {M1: {"fingerprint": None, "scheme": "materialsdb-fp/1", "layers": [{"layer_id": L1A, "thick": 0.2}]}}
    )
    assert _material_status(store_, path, M1) == "unknown"

    store_.set_model_materials(
        path, {M1: {"fingerprint": "x", "scheme": "materialsdb-fp/2", "layers": [{"layer_id": L1A, "thick": 0.2}]}}
    )
    assert _material_status(store_, path, M1) == "unknown"

    # fingerprint present but no org_layer/layers data -> legacy baseline, never changed
    store_.set_model_materials(path, {M1: {"fingerprint": "x", "scheme": "materialsdb-fp/1", "layers": []}})
    assert _material_status(store_, path, M1) == "unknown"


def test_match_layers_id_and_thickness(api):
    from materialsdb.gui.server import _match_layers

    _server, state = api
    store_ = state.resolve_store()

    # layer_id match wins
    result = _match_layers(store_, M1, [{"layer_id": L1A, "thick": 0.2}, {"layer_id": L1B, "thick": 0.1}])
    assert result["state"] == "updatable"
    assert result["mapping"] == {L1A: L1A, L1B: L1B}
    assert result["candidates"] == {}

    # edited layer id -> fall back to thickness
    result = _match_layers(store_, M1, [{"layer_id": "model-x", "thick": 0.2}, {"layer_id": "model-y", "thick": 0.1}])
    assert result["state"] == "updatable"
    assert result["mapping"] == {"model-x": L1A, "model-y": L1B}

    # thick-less model layer with exactly one new layer
    result = _match_layers(store_, M2, [{"layer_id": "model-z", "thick": None}])
    assert result["state"] == "updatable"
    assert result["mapping"] == {"model-z": L2B}

    # thick-less model layer with several new layers -> the user picks
    result = _match_layers(store_, M1, [{"layer_id": "model-z", "thick": 0}])
    assert result["state"] == "ambiguous"
    assert set(result["candidates"]["model-z"]) == {L1A, L1B}

    # no thickness match -> all store layers proposed as candidates
    result = _match_layers(store_, M1, [{"layer_id": "model-q", "thick": 0.999}])
    assert result["state"] == "ambiguous"
    assert set(result["candidates"]["model-q"]) == {L1A, L1B}

    # entity id keys the mapping (legacy model layers have no org_layer id)
    result = _match_layers(store_, M1, [{"entity_id": "42", "layer_id": None, "thick": 0.2}])
    assert result["state"] == "updatable"
    assert result["mapping"] == {"42": L1A}

    # IFC float noise still matches the clean millimetre store layer
    result = _match_layers(store_, M1, [{"layer_id": "model-n", "thick": 0.200000002980232}])
    assert result["state"] == "updatable"
    assert result["mapping"] == {"model-n": L1A}


def test_match_layers_ambiguous(tmp_path):
    from materialsdb.gui.server import _match_layers
    from materialsdb.store import MaterialStore

    xml = tmp_path / "ambig.xml"
    xml.write_text(_AMBIGUOUS_XML, encoding="utf-8")
    store_ = MaterialStore(db_path=tmp_path / "ambig.db")
    store_.refresh(paths=[xml])
    try:
        result = _match_layers(store_, _AMBIG_ID, [{"layer_id": "model-x", "thick": 0.1}])
        assert result["state"] == "ambiguous"
        assert result["candidates"] == {"model-x": [_AMBIG_LAYER_A, _AMBIG_LAYER_B]}
        assert result["mapping"] == {}
    finally:
        store_.close()


def test_match_layers_btk_is_unmatched(api):
    from materialsdb.gui.server import _match_layers

    _server, state = api
    result = _match_layers(state.resolve_store(), M4, [{"layer_id": "x", "thick": 0.2}])
    assert result["state"] == "unmatched"


def test_model_changes_without_model(api):
    server, _state = api
    status, payload = request(server, "GET", "/api/model/changes")
    assert status == 200
    assert payload == {
        "model_path": None,
        "seen_at": None,
        "counts": {"changed": 0, "gone": 0, "unknown": 0},
        "changed": [],
        "gone": [],
        "unknown": [],
    }


def test_model_changes_endpoint_counts_report_and_matching(api):
    from materialsdb.gui.listener import build_add_materials_payload

    server, state = api
    store_ = state.resolve_store()
    path = "/m/changes.ifc"
    _ingest_model(
        server,
        state,
        path,
        {
            M1: {
                "fingerprint": "stale",
                "scheme": "materialsdb-fp/1",
                "layers": [{"layer_id": L1A, "thick": 0.2}, {"layer_id": L1B, "thick": 0.1}],
            },
            M2: {"fingerprint": None, "scheme": "materialsdb-fp/1", "layers": [{"layer_id": L2B, "thick": 0.15}]},
            "ghost": {"fingerprint": "ffff", "scheme": "materialsdb-fp/1", "layers": [{"layer_id": "g", "thick": 0.3}]},
        },
    )

    # record a push snapshot for M1 whose stored name differs from the store
    payload, _missing = build_add_materials_payload(store_, [{"id": M1}])
    snapshot = dict(payload["materials"][0])
    snapshot["name"] = "Old name"
    store_.record_pushed_materials(path, [snapshot])

    status, body = request(server, "GET", "/api/model/changes")
    assert status == 200
    assert body["model_path"] == path
    assert body["seen_at"] is not None
    assert body["counts"] == {"changed": 1, "gone": 1, "unknown": 1}
    assert body["gone"] == ["ghost"]
    assert body["unknown"] == [M2]

    assert len(body["changed"]) == 1
    item = body["changed"][0]
    assert item["material_id"] == M1
    assert set(item) == {"material_id", "matching", "report", "has_snapshot"}
    assert item["matching"]["state"] == "updatable"
    assert item["matching"]["mapping"] == {L1A: L1A, L1B: L1B}
    assert item["has_snapshot"] is True
    assert {"field": "name", "old": "Old name", "new": "Isolant A"} in item["report"]


def test_model_changes_no_snapshot_report_empty(api):
    server, state = api
    path = "/m/nosnap.ifc"
    _ingest_model(
        server,
        state,
        path,
        {M1: {"fingerprint": "stale", "scheme": "materialsdb-fp/1", "layers": [{"layer_id": L1A, "thick": 0.2}]}},
    )
    status, body = request(server, "GET", "/api/model/changes")
    assert status == 200
    assert len(body["changed"]) == 1
    assert body["changed"][0]["report"] == []
    assert body["changed"][0]["has_snapshot"] is False


def test_model_changes_report_pairs_rekeyed_snapshot_layer(api):
    """The snapshot is keyed by the model layer id, which upstream may have
    re-keyed; a same-thickness layer match must still yield the report."""
    from materialsdb.gui.listener import build_add_materials_payload

    server, state = api
    store_ = state.resolve_store()
    path = "/m/rekeyed.ifc"
    old_layer_id = "00000000-0000-0000-0000-00000000dead"
    _ingest_model(
        server,
        state,
        path,
        {
            M2: {
                "fingerprint": "stale",
                "scheme": "materialsdb-fp/1",
                "layers": [{"layer_id": old_layer_id, "thick": 0.15}],
            }
        },
    )

    payload, _missing = build_add_materials_payload(store_, [{"id": M2}])
    snapshot = dict(payload["materials"][0])
    snapshot["layer_id"] = old_layer_id  # pushed under the now-stale model layer id
    snapshot["name"] = "Old B"
    store_.record_pushed_materials(path, [snapshot])

    status, body = request(server, "GET", "/api/model/changes")
    assert status == 200
    assert body["counts"]["changed"] == 1
    item = body["changed"][0]
    assert item["matching"]["state"] == "updatable"
    assert item["matching"]["mapping"] == {old_layer_id: L2B}
    assert item["has_snapshot"] is True
    assert {"field": "name", "old": "Old B", "new": "Beton B"} in item["report"]


def test_model_changes_report_unmatched_layer_still_shows_scalars(api):
    """A model layer that matches no current layer still contributes the
    material-level scalar differences and marks the snapshot present."""
    from materialsdb.gui.listener import build_add_materials_payload

    server, state = api
    store_ = state.resolve_store()
    path = "/m/unmatched.ifc"
    old_layer_id = "00000000-0000-0000-0000-00000000beef"
    _ingest_model(
        server,
        state,
        path,
        {
            M1: {
                "fingerprint": "stale",
                "scheme": "materialsdb-fp/1",
                "layers": [{"layer_id": old_layer_id, "thick": 0.999}],
            }
        },
    )

    payload, _missing = build_add_materials_payload(store_, [{"id": M1}])
    snapshot = dict(payload["materials"][0])
    snapshot["layer_id"] = old_layer_id
    snapshot["name"] = "Old A"
    store_.record_pushed_materials(path, [snapshot])

    status, body = request(server, "GET", "/api/model/changes")
    assert status == 200
    item = body["changed"][0]
    # no thickness match with several store layers: the user is offered
    # candidates rather than an unusable material
    assert item["matching"]["state"] == "ambiguous"
    assert item["matching"]["candidates"]
    assert item["has_snapshot"] is True
    assert {"field": "name", "old": "Old A", "new": "Isolant A"} in item["report"]


def test_model_force_update_queues_used_materials(api):
    """The force endpoint queues in-place updates for every used material that
    resolves, regardless of its fingerprint state."""
    server, state = api
    store_ = state.resolve_store()
    path = "/m/force.ifc"
    _ingest_model(
        server,
        state,
        path,
        {
            M1: {
                "fingerprint": store_.material_fingerprint(M1),  # current: force still queues
                "scheme": "materialsdb-fp/1",
                "used_in": ["IfcSlab"],
                "layers": [{"entity_id": "77", "layer_id": L1A, "thick": 0.2}],
            },
            M2: {
                "fingerprint": None,
                "scheme": None,
                "used_in": [],  # unused -> untouched
                "layers": [{"entity_id": "78", "layer_id": L2B, "thick": 0.15}],
            },
        },
    )

    status, body = request(server, "POST", "/api/model/force-update", payload={}, token=state.token)

    assert status == 200
    assert body["counts"]["queued"] == 1
    assert body["counts"]["unresolved"] == 0
    entry = state.listeners["c1"]["pending"]["materials"][0]
    assert entry["mode"] == "update" and entry["force"] is True
    assert entry["update"] == {"77": L1A}


def test_model_force_update_reports_unresolved_layers(api):
    server, state = api
    path = "/m/force-unresolved.ifc"
    _ingest_model(
        server,
        state,
        path,
        {
            M1: {
                "fingerprint": "stale",
                "scheme": "materialsdb-fp/1",
                "used_in": ["IfcWall"],
                "layers": [{"entity_id": "9", "layer_id": None, "thick": 0.999}],
            }
        },
    )

    status, body = request(server, "POST", "/api/model/force-update", payload={}, token=state.token)

    assert status == 200
    assert body["counts"]["queued"] == 0
    assert body["counts"]["unresolved"] == 1
    layers = body["unresolved"][M1]
    assert layers[0]["entity_id"] == "9"
    assert set(layers[0]["candidates"]) == {L1A, L1B}


def test_model_changes_no_listener_serves_cached_seen_at(api):
    server, state = api
    store_ = state.resolve_store()
    path = "/m/offline.ifc"
    # no listener registered: the model map is only cached by path
    _ingest_model(
        server,
        state,
        path,
        {
            M2: {
                "fingerprint": store_.material_fingerprint(M2),
                "scheme": "materialsdb-fp/1",
                "layers": [{"layer_id": L2B, "thick": 0.15}],
            }
        },
        register=False,
    )

    status, body = request(server, "GET", "/api/model/changes")
    assert status == 200
    assert body["model_path"] == path
    assert body["seen_at"] is not None
    assert body["counts"] == {"changed": 0, "gone": 0, "unknown": 0}
    assert body["changed"] == [] and body["gone"] == [] and body["unknown"] == []


def test_materials_rows_carry_model_status(api):
    server, state = api
    path = "/m/list.ifc"
    _ingest_model(
        server,
        state,
        path,
        {M1: {"fingerprint": "stale", "scheme": "materialsdb-fp/1", "layers": [{"layer_id": L1A, "thick": 0.2}]}},
    )
    status, payload = request(server, "GET", "/api/materials?category=Insulation")
    assert status == 200
    by_id = {row["id"]: row for row in payload["materials"]}
    assert by_id[M1]["model"] == "changed"
    assert by_id[M4]["model"] == "absent"


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


def test_composer_push_list_consume(api):
    server, state = api
    body = {
        "name": "Mur 20+16",
        "design_usage": "consDesignForWall",
        "layers": [
            {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.2},
            {"material_id": None, "thickness_m": 0.18, "placeholder": {"name": "Brique", "lambda_value": 0.21}},
        ],
    }
    status, _ = request(server, "POST", "/api/composer/push", payload=body, token=state.token)
    assert status == 200

    status, listed = request(server, "GET", "/api/composer/incoming", token=state.token)
    assert status == 200
    assert listed["incoming"][0]["name"] == "Mur 20+16"
    assert listed["incoming"][0]["layers"][1]["placeholder"] == {"name": "Brique", "lambda_value": 0.21}

    status, consumed = request(
        server, "POST", "/api/composer/incoming/consume", payload={"index": 0}, token=state.token
    )
    assert status == 200
    assert consumed["construction"]["name"] == "Mur 20+16"
    assert request(server, "GET", "/api/composer/incoming", token=state.token)[1]["incoming"] == []


def test_composer_push_validation(api):
    server, state = api
    status, body = request(server, "POST", "/api/composer/push", payload={"name": "", "layers": []}, token=state.token)
    assert status == 400
    assert "name required" in body["error"]
    status, body = request(server, "POST", "/api/composer/push", payload={"name": "x", "layers": []}, token=state.token)
    assert status == 400
    assert "at least one layer required" in body["error"]
    status, body = request(
        server,
        "POST",
        "/api/composer/push",
        payload={"name": "x", "layers": [{"material_id": None, "thickness_m": -1}]},
        token=state.token,
    )
    assert status == 400
    assert "thickness must be >= 0" in body["error"]


def test_composer_push_rejects_non_dict_placeholder(api):
    server, state = api
    status, body = request(
        server,
        "POST",
        "/api/composer/push",
        payload={"name": "x", "layers": [{"material_id": None, "thickness_m": 0.1, "placeholder": "Brique"}]},
        token=state.token,
    )
    assert status == 400
    assert "invalid placeholder" in body["error"]


def test_composer_consume_bad_index(api):
    server, state = api
    status, body = request(server, "POST", "/api/composer/incoming/consume", payload={"index": 3}, token=state.token)
    assert status == 404
    assert "no incoming at index 3" in body["error"]


def test_composer_incoming_capped_at_20(api):
    server, state = api
    for index in range(22):
        status, _ = request(
            server,
            "POST",
            "/api/composer/push",
            payload={
                "name": f"c{index}",
                "layers": [
                    {"material_id": None, "thickness_m": 0.1, "placeholder": {"name": "", "lambda_value": None}}
                ],
            },
            token=state.token,
        )
        assert status == 200
    status, listed = request(server, "GET", "/api/composer/incoming", token=state.token)
    assert len(listed["incoming"]) == 20
    assert listed["incoming"][0]["name"] == "c21"  # newest first


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

    status, _ = request(
        server, "POST", "/api/schemes/Mon%20Palett%C3%A9/rename", payload={"new_name": "Palette 2"}, token=state.token
    )
    assert status == 200
    assert request(server, "GET", "/api/config")[1]["scheme"] == "Palette 2"

    status, _ = request(server, "POST", "/api/schemes/Palette%202/delete", payload={}, token=state.token)
    assert status == 200
    assert request(server, "GET", "/api/config")[1]["scheme"] == "Lesosai"


def test_scheme_api_rejects_builtin_and_invalid(api):
    server, state = api
    assert (
        request(server, "POST", "/api/schemes/Lesosai", payload={"categories": _custom_palette()}, token=state.token)[0]
        == 400
    )
    assert (
        request(server, "POST", "/api/schemes/import", payload={"categories": _custom_palette()}, token=state.token)[0]
        == 400
    )
    assert (
        request(
            server,
            "POST",
            "/api/schemes/Bad",
            payload={"categories": {"Concrete": {"hatch": "", "color": [0, 0, 0]}}},
            token=state.token,
        )[0]
        == 400
    )
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
    status, _ = request(server, "POST", "/api/config", payload={"ignore_producer_color": True}, token=state.token)
    assert status == 200
    body = request(server, "GET", "/api/config")[1]
    assert body["ignore_producer_color"] is True
    assert body["scheme"] == "Lesosai"
    assert "Lesosai" in body["schemes"]
