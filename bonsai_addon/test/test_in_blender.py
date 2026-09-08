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


def test_full_listener_roundtrip(tmp_path):
    """Server subprocess + real Blender/Bonsai + our poll timer: a send must
    land materials in tool.Ifc.get() and report 'applied' back."""
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
    try:
        info = _wait_for_gui_info(cache_dir)
        port, token = info["port"], info["token"]

        producers = cache_dir / "materialsdb" / "Producers"
        producers.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "mini_producer.xml", producers / "mini_producer.xml")
        status, _ = _request(port, token, "POST", "/api/refresh", {})
        assert status == 200

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
        server.terminate()
        server.wait(timeout=10)
        if old_cache_env is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = old_cache_env
