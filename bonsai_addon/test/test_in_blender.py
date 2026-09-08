"""End-to-end verification of the materialsdb add-on inside real Blender+Bonsai.

Run: pytest bonsai_addon/test/test_in_blender.py -q  (pytest-blender active)
Local-only: CI runners have no Blender/Bonsai."""

import http.client
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from bonsai import tool

import bonsai_addon
from bonsai_addon import insert
from bonsai_addon.discovery import ListenerClient

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"

PAYLOAD = {
    "action": "add_materials",
    "materials": [
        {
            "source_id": "00000000-0000-0000-0000-000000000001",
            "layer_id": "00000000-0000-0000-0000-0000000000a1",
            "name": "Isolant A",
            "description": "Panneau isolant",
            "category": "Insulation",
            "color": 16711680,
            "identity": {
                "material_id": "00000000-0000-0000-0000-000000000001",
                "company_id": "A1B85A67-5B1E-4960-A297-2DE8275049C5",
                "company": "Mini SA",
            },
            "psets": {"materialsdb.org_layer": {"layer_id": "00000000-0000-0000-0000-0000000000a1", "thick": 0.2}},
            "layer": {"layer_id": "00000000-0000-0000-0000-0000000000a1", "thick_m": 0.2},
        },
        {
            "source_id": "00000000-0000-0000-0000-000000000001",
            "layer_id": "00000000-0000-0000-0000-0000000000a2",
            "name": "Isolant A",
            "description": "Panneau isolant",
            "category": "Insulation",
            "color": 16711680,
            "identity": {
                "material_id": "00000000-0000-0000-0000-000000000001",
                "company_id": "A1B85A67-5B1E-4960-A297-2DE8275049C5",
                "company": "Mini SA",
            },
            "psets": {"materialsdb.org_layer": {"layer_id": "00000000-0000-0000-0000-0000000000a2", "thick": 0.1}},
            "layer": {"layer_id": "00000000-0000-0000-0000-0000000000a2", "thick_m": 0.1},
        },
    ],
}


def test_insert_into_bonsai_project():
    file = tool.Ifc.get()
    assert file is not None

    created = insert.apply_add_materials(file, PAYLOAD)

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
    layers = sorted(file.by_type("IfcMaterialLayer"), key=lambda l: l.LayerThickness)
    assert [round(l.LayerThickness, 3) for l in layers] == [0.1, 0.2]
    assert {l.Description for l in layers} == {
        "00000000-0000-0000-0000-0000000000a1",
        "00000000-0000-0000-0000-0000000000a2",
    }


def _request(port, token, method, path, payload=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"X-MaterialsDB-Token": token}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    if response.status == 204 or not data:
        return response.status, None
    return response.status, json.loads(data)


def _wait_for_gui_info(cache_dir, timeout=15.0):
    path = cache_dir / "materialsdb" / "gui.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.2)
    raise RuntimeError(f"gui.json never appeared at {path}")


def _seeded_server(tmp_path):
    cache_dir = tmp_path / "cache"
    old_cache_env = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = str(cache_dir)  # discovery reads env per call
    python3 = shutil.which("python3")
    assert python3, "system python3 with lxml required to host the server subprocess"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    server = subprocess.Popen(
        [python3, "-m", "materialsdb.gui", "--no-browser", "--port", "0"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    info = _wait_for_gui_info(cache_dir)
    producers = cache_dir / "materialsdb" / "Producers"
    producers.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "mini_producer.xml", producers / "mini_producer.xml")
    status, _ = _request(info["port"], info["token"], "POST", "/api/refresh", {})
    assert status == 200
    return server, cache_dir, info, old_cache_env


def _stop_server(server, old_cache_env):
    server.terminate()
    server.wait(timeout=10)
    if old_cache_env is None:
        os.environ.pop("XDG_CACHE_HOME", None)
    else:
        os.environ["XDG_CACHE_HOME"] = old_cache_env


def test_full_listener_roundtrip(tmp_path):
    """Server subprocess + real Blender/Bonsai + our poll timer: a send must
    land materials in tool.Ifc.get() and report 'applied' back."""
    server, _cache_dir, info, old_cache_env = _seeded_server(tmp_path)
    try:
        port, token = info["port"], info["token"]

        client = ListenerClient()
        client.register(bonsai_addon._model_path())

        status, body = _request(
            port,
            token,
            "POST",
            "/api/listener/send",
            {
                "client_id": client.client_id,
                "action": "add_materials",
                "items": [{"id": "00000000-0000-0000-0000-000000000001"}],
            },
        )
        assert status == 200, body
        assert body["queued"] == 2

        previous = bonsai_addon._CLIENT
        bonsai_addon._CLIENT = client
        try:
            assert bonsai_addon._poll_timer() == 1.0  # keeps polling
        finally:
            bonsai_addon._CLIENT = previous

        file = tool.Ifc.get()
        assert file is not None
        assert len(file.by_type("IfcMaterial")) == 2
        identity = [p for p in file.by_type("IfcMaterialProperties") if p.Name == "materialsdb"]
        assert len(identity) == 2

        status, body = _request(port, token, "GET", "/api/listener/clients")
        assert status == 200
        assert body["clients"][0]["last_status"] == {"status": "applied", "detail": "2 material(s) added"}
    finally:
        _stop_server(server, old_cache_env)


def test_construction_roundtrip_and_undo(tmp_path):
    """Construction push lands an IfcWallType + layer set in the live model,
    a re-send modifies the SAME set in place, and Blender undo/redo works."""
    import bpy

    server, _cache_dir, info, old_cache_env = _seeded_server(tmp_path)
    try:
        port, token = info["port"], info["token"]
        client = ListenerClient()
        client.register(bonsai_addon._model_path())

        def send(construction):
            status, body = _request(
                port,
                token,
                "POST",
                "/api/listener/send",
                {"client_id": client.client_id, "action": "add_construction", "construction": construction},
            )
            assert status == 200, body

        construction = {
            "name": "Mur 20+16",
            "design_usage": "consDesignForWall",
            "layers": [
                {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.22},
                {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            ],
        }
        send(construction)

        previous = bonsai_addon._CLIENT
        bonsai_addon._CLIENT = client
        try:
            assert bonsai_addon._poll_timer() == 1.0
        finally:
            bonsai_addon._CLIENT = previous
        # Background sessions start with Blender's undo system disabled and
        # UNDO operators push no steps there (in the GUI the window manager
        # pushes them); an explicit undo_push initializes the system and
        # captures the post-apply state so ed.undo()/ed.redo() exercise the
        # same props.last_transaction wiring Ctrl+Z uses.
        bpy.ops.ed.undo_push(message="materialsdb push 1")

        file = tool.Ifc.get()
        assert file is not None
        wall_types = file.by_type("IfcWallType")
        assert len(wall_types) == 1
        assert wall_types[0].Name == "Mur 20+16"
        assert wall_types[0].GlobalId
        layer_set = wall_types[0].HasAssociations[0].RelatingMaterial
        assert layer_set.is_a("IfcMaterialLayerSet")
        assert {round(l.LayerThickness, 3) for l in layer_set.MaterialLayers} == {0.22, 0.15}

        # re-send with a modified thickness: same set entity updated in place
        set_step_id = layer_set.id()
        modified = {
            "name": "Mur 20+16",
            "design_usage": "consDesignForWall",
            "layers": [
                {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.25},
                {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
            ],
        }
        send(modified)
        bonsai_addon._CLIENT = client
        try:
            assert bonsai_addon._poll_timer() == 1.0
        finally:
            bonsai_addon._CLIENT = previous
        bpy.ops.ed.undo_push(message="materialsdb push 2")
        assert len(file.by_type("IfcWallType")) == 1  # upsert, no duplicate
        assert len(file.by_type("IfcMaterialLayerSet")) == 1
        assert file.by_type("IfcMaterialLayerSet")[0].id() == set_step_id
        assert {round(l.LayerThickness, 3) for l in layer_set.MaterialLayers} == {0.25, 0.15}

        _, body = _request(port, token, "GET", "/api/listener/clients")
        assert body["clients"][0]["last_status"]["status"] == "applied"

        # Blender undo removes the last push; redo restores it
        bpy.ops.ed.undo()
        assert len(file.by_type("IfcMaterialLayerSet")) == 0 or (
            {round(l.LayerThickness, 3) for l in file.by_type("IfcMaterialLayerSet")[0].MaterialLayers} == {0.22, 0.15}
        )
        bpy.ops.ed.redo()
        assert {round(l.LayerThickness, 3) for l in file.by_type("IfcMaterialLayerSet")[0].MaterialLayers} == {
            0.25,
            0.15,
        }
    finally:
        _stop_server(server, old_cache_env)
