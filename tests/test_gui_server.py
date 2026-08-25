import http.client
import json
import threading

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


def test_app_js_served(api):
    server, _ = api
    status, body = request(server, "GET", "/app.js")
    assert status == 200
    assert b"MATERIALSDB_TOKEN" in body


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
    # designusage is not declared in materialsdb103.xsd, so the generated
    # Construction class drops it and the payload degrades to "".
    assert payload["designusage"] == ""

    status, payload = request(server, "GET", "/api/materials/unknown")
    assert status == 404


def test_mutations_require_token(api):
    server, _ = api
    status, _ = request(server, "POST", "/api/refresh", payload={})
    assert status == 403

    status, _ = request(server, "POST", "/api/refresh", payload={}, token="wrong")
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
    status, _ = request(server, "POST", "/api/export", payload={"ids": []}, token=state.token)
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
        server, "POST", "/api/pick", payload={"ids": ["00000000-0000-0000-0000-000000000001"]}, token=token
    )
    assert status == 200 and payload["added"] == 1

    save_as = tmp_path / "project-plus.ifc"
    status, payload = request(server, "POST", "/api/session/save", payload={"path": str(save_as)}, token=token)
    assert status == 200

    reopened = ifcopenshell.open(str(save_as))
    assert {m.Name for m in reopened.by_type("IfcMaterial")} >= {"Isolant A", "Beton B"}


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
        payload={"ids": ["00000000-0000-0000-0000-000000000001"]},
        token=fresh.token,
    )
    assert status == 409


def test_session_open_rejects_bad_path(api, tmp_path):
    server, state = api
    status, _ = request(
        server, "POST", "/api/session/open", payload={"path": str(tmp_path / "nope.ifc")}, token=state.token
    )
    assert status == 400


def test_config_roundtrip(api):
    recorded = pytest.config_recorded  # ty: ignore[unresolved-attribute] - set by pinned_fr_ch_config fixture

    status, _ = request(api[0], "POST", "/api/config", payload={"lang": "en", "country": "FR"}, token=api[1].token)

    assert status == 200
    assert recorded == {"lang": "en", "country": "FR"}
