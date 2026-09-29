"""Regression tests for the add-on discovery client (no live server)."""

import http.client
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bonsai_addon"))

import discovery  # ty: ignore[unresolved-import] - add-on module on a side path


class _StubResponse:
    def __init__(self, status, body=b""):
        self.status = status
        self._body = body

    def read(self):
        return self._body


def _stub_connection(monkeypatch, responses):
    calls = []

    class StubConnection:
        def __init__(self, host, port, timeout=None):
            pass

        def request(self, method, path, body=None, headers=None):
            calls.append((method, path.split("?")[0]))

        def getresponse(self):
            status, body = responses.pop(0)
            return _StubResponse(status, body)

        def close(self):
            pass

    monkeypatch.setattr(discovery, "read_gui_info", lambda: (5959, "tok"))
    monkeypatch.setattr(http.client, "HTTPConnection", StubConnection)
    return calls


def test_poll_unwraps_envelope(monkeypatch):
    """poll() returns the push itself, not the server's {\"payload\": ...} wrapper,
    and None when nothing is pending — the timer feeds the result straight to
    insert.apply_add_materials."""
    push = {"action": "add_materials", "materials": [{"name": "x"}]}
    responses = [(200, json.dumps({"payload": push}).encode()), (204, b"")]
    _stub_connection(monkeypatch, responses)

    client = discovery.ListenerClient()

    assert client.poll() == push
    assert client.poll() is None


def test_reregisters_after_server_restart(monkeypatch):
    """A 404 (stale client_id after a GUI restart) must clear registered_path
    so the next tick re-registers instead of polling a dead path forever."""
    calls = _stub_connection(monkeypatch, [(200, b""), (404, b"unknown client"), (200, b"")])

    client = discovery.ListenerClient()
    client.register("/models/house.ifc")
    assert client.registered_path == "/models/house.ifc"

    with pytest.raises(RuntimeError):
        client.poll()
    assert client.registered_path is None

    client.register("/models/house.ifc")
    assert client.registered_path == "/models/house.ifc"

    registers = [p for method, p in calls if method == "POST" and p == "/api/listener/register"]
    assert registers == ["/api/listener/register", "/api/listener/register"]
    assert [method for method, _ in calls] == ["POST", "GET", "POST"]


def test_clear_gui_info_removes_stale_file(tmp_path, monkeypatch):
    gui_json = tmp_path / "materialsdb" / "gui.json"
    gui_json.parent.mkdir(parents=True)
    gui_json.write_text('{"port": 1, "token": "t", "pid": 2}', encoding="utf-8")
    monkeypatch.setattr(discovery, "_cache_folder", lambda: tmp_path / "materialsdb")

    discovery.clear_gui_info()

    assert not gui_json.exists()
    discovery.clear_gui_info()  # idempotent on absence


def test_send_model_map_posts_materials(monkeypatch):
    """send_model_map POSTs the map to /api/listener/model, carrying the
    client id, model path and its own per-entry scheme."""
    client = discovery.ListenerClient()
    sent = []
    monkeypatch.setattr(client, "_request", lambda method, path, payload=None: sent.append((method, path, payload)))
    materials = {"ID1": {"fingerprint": "f", "scheme": "materialsdb-fp/1", "layers": []}}

    client.send_model_map("/models/house.ifc", materials)

    assert sent == [
        (
            "POST",
            "/api/listener/model",
            {
                "client_id": client.client_id,
                "model_path": "/models/house.ifc",
                "scheme": "materialsdb-fp/1",
                "materials": materials,
            },
        )
    ]


def test_send_model_map_scheme_empty_without_entries(monkeypatch):
    """The top-level scheme is derived from the map's entries (the server
    ignores it); an empty map (or entries without one) yields ""."""
    client = discovery.ListenerClient()
    sent = []
    monkeypatch.setattr(client, "_request", lambda method, path, payload=None: sent.append(payload))

    client.send_model_map("", {})
    client.send_model_map("", {"ID1": {"fingerprint": None, "scheme": None, "layers": []}})

    assert [payload["scheme"] for payload in sent] == ["", ""]
    assert [payload["model_path"] for payload in sent] == ["", ""]


def _scratch_material(file, material_id, *, fingerprint=None, scheme=None, layer_id=None, thick=None):
    """An IfcMaterial carrying insert's identity pset (and optionally an
    org_layer pset), as the add-on writes them on a push."""
    import ifcopenshell.api
    import insert  # ty: ignore[unresolved-import] - add-on module on a side path

    material = ifcopenshell.api.run("material.add_material", file, name=material_id)
    identity = {"material_id": material_id, "company_id": "C", "company": "Co"}
    if fingerprint is not None:
        identity["fingerprint"] = fingerprint
        identity["fingerprint_scheme"] = scheme
    insert._add_identity_pset(file, material, identity)
    if layer_id is not None:
        pset = ifcopenshell.api.run("pset.add_pset", file, product=material, name="materialsdb.org_layer")
        properties = {"layer_id": layer_id}
        if thick is not None:
            properties["thick"] = thick
        ifcopenshell.api.run("pset.edit_pset", file, pset=pset, properties=properties)
    return material


def test_model_material_map_identity_and_layers():
    """The map keys by material_id, reads fingerprint/scheme from the identity
    pset and keeps one layer entry per IfcMaterial (keyed by entity id, so
    legacy materials with no org_layer stay addressable)."""
    ifcopenshell = pytest.importorskip("ifcopenshell")
    api = pytest.importorskip("ifcopenshell.api")

    file = ifcopenshell.file(schema="IFC4")
    m1 = _scratch_material(file, "ID1", fingerprint="fp1", scheme="materialsdb-fp/1", layer_id="L1", thick=0.1)
    m2 = _scratch_material(file, "ID1", fingerprint="fp1", scheme="materialsdb-fp/1", layer_id="L2", thick=0.2)
    m3 = _scratch_material(file, "ID2", fingerprint="fp2", scheme="materialsdb-fp/1")
    m4 = _scratch_material(file, "ID3", layer_id="L3", thick=0.3)  # no fingerprint -> scheme None
    api.run("material.add_material", file, name="plain")  # no identity -> excluded

    assert discovery._model_material_map(file) == {
        "ID1": {
            "fingerprint": "fp1",
            "scheme": "materialsdb-fp/1",
            "used_in": [],
            "layers": [
                {"entity_id": str(m1.id()), "layer_id": "L1", "thick": 0.1},
                {"entity_id": str(m2.id()), "layer_id": "L2", "thick": 0.2},
            ],
        },
        "ID2": {
            "fingerprint": "fp2",
            "scheme": "materialsdb-fp/1",
            "used_in": [],
            "layers": [{"entity_id": str(m3.id()), "layer_id": None, "thick": None}],
        },
        "ID3": {
            "fingerprint": None,
            "scheme": None,
            "used_in": [],
            "layers": [{"entity_id": str(m4.id()), "layer_id": "L3", "thick": 0.3}],
        },
    }


def test_model_material_map_marks_used_materials():
    """Materials referenced by wall/slab/roof associations (types or
    occurrences, directly or via a layer set) are flagged in `used_in`."""
    ifcopenshell = pytest.importorskip("ifcopenshell")
    api = pytest.importorskip("ifcopenshell.api")

    file = ifcopenshell.file(schema="IFC4")
    used = _scratch_material(file, "ID1", layer_id="L1", thick=0.2)
    _scratch_material(file, "ID2", layer_id="L2", thick=0.1)
    wall = api.run("root.create_entity", file, ifc_class="IfcWall", name="W")
    layer_set = api.run("material.add_material_set", file, name="S", set_type="IfcMaterialLayerSet")
    api.run("material.add_layer", file, layer_set=layer_set, material=used)
    api.run("material.assign_material", file, products=[wall], type="IfcMaterialLayerSet", material=layer_set)

    result = discovery._model_material_map(file)
    assert result["ID1"]["used_in"] == ["IfcWall"]
    assert result["ID2"]["used_in"] == []


def test_model_material_map_falls_back_to_layer_thickness():
    """When the org_layer pset carries no thick, the IfcMaterialLayer thickness
    is used (metres), so the server can match layers by thickness."""
    ifcopenshell = pytest.importorskip("ifcopenshell")
    api = pytest.importorskip("ifcopenshell.api")

    file = ifcopenshell.file(schema="IFC4")
    material = _scratch_material(file, "ID1", fingerprint="fp1", scheme="materialsdb-fp/1", layer_id="L1")
    layer_set = api.run("material.add_material_set", file, name="S", set_type="IfcMaterialLayerSet")
    layer = api.run("material.add_layer", file, layer_set=layer_set, material=material)
    api.run("material.edit_layer", file, layer=layer, attributes={"LayerThickness": 0.15})

    assert discovery._model_material_map(file)["ID1"]["layers"] == [
        {"entity_id": str(material.id()), "layer_id": "L1", "thick": 0.15}
    ]
