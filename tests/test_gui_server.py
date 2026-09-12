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
def pinned_fr_ch_config(monkeypatch):
    monkeypatch.setattr("materialsdb.config.get_lang", lambda: "fr")
    monkeypatch.setattr("materialsdb.config.get_country", lambda: "CH")
    recorded = {}
    monkeypatch.setattr(
        "materialsdb.config.set_param",
        lambda param, value: recorded.__setitem__(param, value),
    )
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
