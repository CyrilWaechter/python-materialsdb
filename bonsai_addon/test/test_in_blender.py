"""End-to-end verification of the materialsdb add-on inside real Blender+Bonsai.

Run: pytest bonsai_addon/test/test_in_blender.py -q  (pytest-blender active)
Local-only: CI runners have no Blender/Bonsai."""

import hashlib
import http.client
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import ifcopenshell.api
import ifcopenshell.util.element
import pytest
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


_SEED_CODE = (
    "import sys; from materialsdb import query; s = query.get_store(); s.refresh(paths=sys.argv[1:]); s.close()"
)


def _seed_store(cache_dir, python3, env):
    """Index ONLY the fixture producer into the shared store, synchronously and
    offline. The server opens the same materials.db, so its first request sees
    the materials. (The previous flow POSTed /api/refresh and never awaited the
    async worker: the network download of every producer raced each push, so
    pushes arrived at an unseeded store.)"""
    producers = cache_dir / "materialsdb" / "Producers"
    producers.mkdir(parents=True, exist_ok=True)
    fixture = producers / "mini_producer.xml"
    shutil.copy(FIXTURES / "mini_producer.xml", fixture)
    seeded = subprocess.run(
        [python3, "-c", _SEED_CODE, str(fixture)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert seeded.returncode == 0, seeded.stderr


def _seeded_server(tmp_path):
    cache_dir = tmp_path / "cache"
    old_cache_env = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = str(cache_dir)  # discovery reads env per call
    python3 = shutil.which("python3")
    assert python3, "system python3 with lxml required to host the server subprocess"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    _seed_store(cache_dir, python3, env)
    server = subprocess.Popen(
        [python3, "-m", "materialsdb.gui", "--no-browser", "--port", "0"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    info = _wait_for_gui_info(cache_dir)
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

        # the pushed style must be registered with Bonsai (linked Blender
        # material), or the viewport shows no colour and editing it crashes
        import bpy

        style = next(s for s in file.by_type("IfcSurfaceStyle") if s.Name == "color 16711680")
        assert isinstance(tool.Ifc.get_object(style), bpy.types.Material)

        status, body = _request(port, token, "GET", "/api/listener/clients")
        assert status == 200
        assert body["clients"][0]["last_status"] == {"status": "applied", "detail": "2 material(s) added"}
    finally:
        _stop_server(server, old_cache_env)


def test_construction_roundtrip_and_undo(tmp_path):
    """Construction push lands an IfcWallType + layer set in the live model,
    a re-send modifies the SAME set in place, and Blender undo/redo works."""
    import bpy

    server = None
    old_cache_env = None
    try:
        server, _cache_dir, info, old_cache_env = _seeded_server(tmp_path)
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
        file = tool.Ifc.get()
        assert file is not None
        wall_types = file.by_type("IfcWallType")
        assert len(wall_types) == 1
        assert wall_types[0].Name == "Mur 20+16"
        assert wall_types[0].GlobalId
        # U-value pset written with the wall-direction Rsi/Rse (server-side)
        props = ifcopenshell.util.element.get_pset(wall_types[0], name="Pset_WallCommon")
        assert props is not None and "ThermalTransmittance" in props
        layer_set = wall_types[0].HasAssociations[0].RelatingMaterial
        assert layer_set.is_a("IfcMaterialLayerSet")
        assert {round(l.LayerThickness, 3) for l in layer_set.MaterialLayers} == {0.22, 0.15}
        # bonsai-native linking: the pushed type is visible in the outliner
        # (object in the IfcTypeProduct project-child collection)
        status, body = _request(port, token, "GET", "/api/listener/clients")
        obj = bpy.data.objects.get("IfcWallType/Mur 20+16")  # tool.Loader.get_name prefixes the class
        assert obj is not None
        assert any(coll.name == "IfcTypeProduct" for coll in obj.users_collection)

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
        assert len(file.by_type("IfcWallType")) == 1  # upsert, no duplicate
        assert len(file.by_type("IfcMaterialLayerSet")) == 1
        assert file.by_type("IfcMaterialLayerSet")[0].id() == set_step_id
        assert {round(l.LayerThickness, 3) for l in layer_set.MaterialLayers} == {0.25, 0.15}

        status, body = _request(port, token, "GET", "/api/listener/clients")
        assert status == 200
        assert body["clients"][0]["last_status"]["status"] == "applied"

        # Blender undo removes the last push; redo restores it. The pushed
        # type object came from push 1, so it survives this first undo.
        bpy.ops.ed.undo()
        assert len(file.by_type("IfcMaterialLayerSet")) == 0 or (
            {round(l.LayerThickness, 3) for l in file.by_type("IfcMaterialLayerSet")[0].MaterialLayers} == {0.22, 0.15}
        )
        assert bpy.data.objects.get("IfcWallType/Mur 20+16") is not None
        bpy.ops.ed.redo()
        assert {round(l.LayerThickness, 3) for l in file.by_type("IfcMaterialLayerSet")[0].MaterialLayers} == {
            0.25,
            0.15,
        }
    finally:
        if server is not None:
            _stop_server(server, old_cache_env)


def test_undo_removes_pushed_type_and_object(tmp_path):
    """A full undo rewinds the pushed construction AND its outliner object."""
    import bpy

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
                "action": "add_construction",
                "construction": {
                    "name": "Mur 20+16",
                    "design_usage": "consDesignForWall",
                    "layers": [
                        {"material_id": "00000000-0000-0000-0000-000000000001", "thickness_m": 0.22},
                        {"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15},
                    ],
                },
            },
        )
        assert status == 200, body

        # marker BEFORE the push: undo() then reverts to this snapshot
        # (the timer pushes its own titled step after a successful apply)
        bpy.ops.ed.undo_push(message="pre-push")
        previous = bonsai_addon._CLIENT
        bonsai_addon._CLIENT = client
        try:
            assert bonsai_addon._poll_timer() == 1.0
        finally:
            bonsai_addon._CLIENT = previous

        file = tool.Ifc.get()
        assert len(file.by_type("IfcWallType")) == 1
        assert bpy.data.objects.get("IfcWallType/Mur 20+16") is not None

        bpy.ops.ed.undo()
        assert bpy.data.objects.get("IfcWallType/Mur 20+16") is None
        assert len(file.by_type("IfcWallType")) == 0
        assert len(file.by_type("IfcMaterialLayerSet")) == 0
    finally:
        if server is not None:
            _stop_server(server, old_cache_env)


def test_construction_roundtrip_from_model(tmp_path):
    """Select an occurrence, push its layer set to the composer, consume it,
    edit, push back: same set entity updated, placeholder material re-attached
    by name (no duplicate IfcMaterial)."""
    import bpy

    server, _cache_dir, info, old_cache_env = _seeded_server(tmp_path)
    try:
        port, token = info["port"], info["token"]
        file = tool.Ifc.get()

        # build a model-side type + occurrence with a mixed layer set
        type_element = ifcopenshell.api.run("root.create_entity", file, ifc_class="IfcWallType", name="Mur 20+16")
        occurrence = ifcopenshell.api.run("root.create_entity", file, ifc_class="IfcWall", name="Mur occurrence")
        ifcopenshell.api.run("type.assign_type", file, related_objects=[occurrence], relating_type=type_element)
        layer_set = ifcopenshell.api.run(
            "material.add_material_set", file, name="Mur 20+16", set_type="IfcMaterialLayerSet"
        )
        ifcopenshell.api.run(
            "material.assign_material", file, products=[type_element], type="IfcMaterialLayerSet", material=layer_set
        )
        resolvable = ifcopenshell.api.run("material.add_material", file, name="Isolant A")
        identity = ifcopenshell.api.run("pset.add_pset", file, product=resolvable, name="materialsdb")
        ifcopenshell.api.run(
            "pset.edit_pset", file, pset=identity, properties={"material_id": "00000000-0000-0000-0000-000000000001"}
        )
        # reuse keys on (material_id, org_layer.layer_id): this is the already
        # pushed material for the store's a1 layer (thickness 0.2 m)
        org_layer = ifcopenshell.api.run("pset.add_pset", file, product=resolvable, name="materialsdb.org_layer")
        ifcopenshell.api.run(
            "pset.edit_pset", file, pset=org_layer, properties={"layer_id": "00000000-0000-0000-0000-0000000000a1"}
        )
        foreign = ifcopenshell.api.run("material.add_material", file, name="Brique terrecuite")
        thermal = ifcopenshell.api.run("pset.add_pset", file, product=foreign, name="Pset_MaterialThermal")
        ifcopenshell.api.run("pset.edit_pset", file, pset=thermal, properties={"ThermalConductivity": 0.21})
        layer_a = ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=resolvable)
        layer_b = ifcopenshell.api.run("material.add_layer", file, layer_set=layer_set, material=foreign)
        ifcopenshell.api.run("material.edit_layer", file, layer=layer_a, attributes={"LayerThickness": 0.2})
        ifcopenshell.api.run("material.edit_layer", file, layer=layer_b, attributes={"LayerThickness": 0.18})

        # an active occurrence object so the panel operator has a selection
        obj = bpy.data.objects.new("Mur occurrence", None)
        tool.Ifc.link(occurrence, obj)
        tool.Root.set_object_name(obj, occurrence)
        tool.Collector.assign(obj)
        bpy.context.view_layer.objects.active = obj

        previous = bonsai_addon._CLIENT
        bonsai_addon._CLIENT = client = ListenerClient()
        client.register(bonsai_addon._model_path())
        try:
            assert bpy.ops.materialsdb.send_construction() == {"FINISHED"}
        finally:
            bonsai_addon._CLIENT = previous

        status, listed = _request(port, token, "GET", "/api/composer/incoming")
        assert status == 200
        assert listed["incoming"][0]["name"] == "Mur 20+16"
        assert listed["incoming"][0]["layers"][1]["placeholder"] == {"name": "Brique terrecuite", "lambda_value": 0.21}

        # composer side: consume + edit (thickness change) + push back
        status, consumed = _request(port, token, "POST", "/api/composer/incoming/consume", payload={"index": 0})
        assert status == 200
        construction = consumed["construction"]
        construction["layers"][0]["thickness_m"] = 0.25
        status, body = _request(
            port,
            token,
            "POST",
            "/api/listener/send",
            {"client_id": client.client_id, "action": "add_construction", "construction": construction},
        )
        assert status == 200, body

        materials_before = len(file.by_type("IfcMaterial"))
        bonsai_addon._CLIENT = client
        try:
            assert bonsai_addon._poll_timer() == 1.0
        finally:
            bonsai_addon._CLIENT = previous

        layer_set_after = file.by_type("IfcMaterialLayerSet")[0]
        assert {round(l.LayerThickness, 3) for l in layer_set_after.MaterialLayers} == {0.25, 0.18}
        assert len(file.by_type("IfcMaterial")) == materials_before  # placeholder re-attached, not duplicated
        assert len(file.by_type("IfcWallType")) == 1
        assert bpy.data.objects.get("IfcWallType/Mur 20+16") is not None  # still outliner-linked
    finally:
        if server is not None:
            _stop_server(server, old_cache_env)


MATERIAL_ID = "00000000-0000-0000-0000-000000000001"
LAYER_A1 = "00000000-0000-0000-0000-0000000000a1"


def _material_on_layer(file, layer_id):
    """The IfcMaterial whose identity maps it to `layer_id`."""
    for material in file.by_type("IfcMaterial"):
        org_layer = ifcopenshell.util.element.get_psets(material).get("materialsdb.org_layer") or {}
        if org_layer.get("layer_id") == layer_id:
            return material
    raise AssertionError(f"no IfcMaterial for layer {layer_id}")


def _mutate_stored_material(cache_dir, material_id, replacements):
    """Byte-replace the material's XML blob in the server's store and return the
    new fingerprint (sha256 of the blob, matching store.material_fingerprint).
    This models the producer revising a material upstream."""
    connection = sqlite3.connect(cache_dir / "materialsdb" / "materials.db")
    try:
        row = connection.execute("SELECT xml FROM materials WHERE id=?", (material_id,)).fetchone()
        assert row is not None, f"{material_id} missing from the seeded store"
        xml = bytes(row[0])
        for old, new in replacements:
            xml = xml.replace(old, new)
        connection.execute("UPDATE materials SET xml=? WHERE id=?", (sqlite3.Binary(xml), material_id))
        connection.commit()
    finally:
        connection.close()
    return hashlib.sha256(xml).hexdigest()


def _stored_model_fingerprint(cache_dir, material_id):
    connection = sqlite3.connect(cache_dir / "materialsdb" / "materials.db")
    try:
        row = connection.execute(
            "SELECT fingerprint FROM model_materials WHERE material_id=?", (material_id,)
        ).fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def test_material_update_preserves_guid_and_resends_model_map(tmp_path):
    """A producer revision + an opted-in ``mode="update"`` push refreshes the
    SAME IfcMaterial in place (its entity kept — IfcMaterial has no GlobalId, so
    the STEP id is its identity — and the resolved pset value updated) and
    reports the model map back to the GUI afterwards."""
    server, cache_dir, info, old_cache_env = _seeded_server(tmp_path)
    try:
        port, token = info["port"], info["token"]
        client = ListenerClient()
        client.register(bonsai_addon._model_path())

        def push(item):
            status, body = _request(
                port,
                token,
                "POST",
                "/api/listener/send",
                {"client_id": client.client_id, "action": "add_materials", "items": [item]},
            )
            assert status == 200, body
            return body

        def drain():
            previous = bonsai_addon._CLIENT
            bonsai_addon._CLIENT = client
            try:
                assert bonsai_addon._poll_timer() == 1.0
            finally:
                bonsai_addon._CLIENT = previous

        push({"id": MATERIAL_ID})
        drain()

        file = tool.Ifc.get()
        assert file is not None
        material = _material_on_layer(file, LAYER_A1)
        entity_id = material.id()  # IfcMaterial is not rooted: its STEP id is its identity
        thermal = ifcopenshell.util.element.get_pset(material, name="Pset_MaterialThermal")
        assert thermal["ThermalConductivity"] == pytest.approx(0.036)

        # the seeded store's XML changes (λ 0.036 -> 0.06), so its fingerprint does
        fingerprint = _mutate_stored_material(
            cache_dir, MATERIAL_ID, [(b'lambda_value="0.036"', b'lambda_value="0.06"')]
        )

        # re-push just that layer, opted into the in-place update mapping
        push({"id": MATERIAL_ID, "layer_ids": [LAYER_A1], "mode": "update", "update": {LAYER_A1: LAYER_A1}})

        sent = []
        original_send_model_map = client.send_model_map

        def spy(model_path, materials):
            sent.append((model_path, materials))
            return original_send_model_map(model_path, materials)

        client.send_model_map = spy
        drain()

        # refreshed in place: same entity, no duplicate material
        assert _material_on_layer(file, LAYER_A1).id() == entity_id
        assert len(file.by_type("IfcMaterial")) == 2
        thermal = ifcopenshell.util.element.get_pset(_material_on_layer(file, LAYER_A1), name="Pset_MaterialThermal")
        assert thermal["ThermalConductivity"] == pytest.approx(0.06)

        # the push reported the model map back to the GUI, with the new fingerprint
        assert len(sent) == 1, sent
        model_path, materials = sent[0]
        assert model_path == bonsai_addon._model_path()
        assert materials[MATERIAL_ID]["fingerprint"] == fingerprint
        assert _stored_model_fingerprint(cache_dir, MATERIAL_ID) == fingerprint
    finally:
        _stop_server(server, old_cache_env)


def _base_colour(blender_material):
    return tuple(blender_material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value)


def test_restyle_refreshes_reused_style_and_live_material():
    """A restyle push reuses the existing IfcSurfaceStyle (same name) and
    recolours it; the linked Blender material's Principled BSDF Base Color must
    follow so the viewport shows the new colour immediately. If this fails on
    the Base Color assertion, _sync_style_materials must refresh already-linked
    Blender materials (it currently only calls tool.Style.switch_shading)."""
    import bpy

    file = tool.Ifc.get()
    assert file is not None
    assert insert.apply_add_materials(file, PAYLOAD) == 2
    bonsai_addon._sync_style_materials(file)

    style = next(s for s in file.by_type("IfcSurfaceStyle") if s.Name == "color 16711680")
    blender_material = tool.Ifc.get_object(style)
    assert isinstance(blender_material, bpy.types.Material)
    base_before = _base_colour(blender_material)

    class _StubClient:
        def __init__(self):
            self.calls = []

        def report(self, status, detail):
            self.calls.append((status, detail))

    previous = bonsai_addon._CLIENT
    client = _StubClient()
    bonsai_addon._CLIENT = client
    bonsai_addon._PENDING = {
        "action": "restyle",
        "materials": [{"material_id": MATERIAL_ID, "style_name": "color 16711680", "style_color": [0.0, 1.0, 0.0]}],
    }
    try:
        assert bpy.ops.materialsdb.apply_push() == {"FINISHED"}
    finally:
        bonsai_addon._CLIENT = previous
        bonsai_addon._PENDING = None

    assert client.calls == [("applied", "2 material(s) restyled")]
    colour = next(s for s in style.Styles if s.is_a("IfcSurfaceStyleShading")).SurfaceColour
    assert (round(colour.Red * 255), round(colour.Green * 255), round(colour.Blue * 255)) == (0, 255, 0)
    assert tool.Ifc.get_object(style) is blender_material  # same style, same link
    assert _base_colour(blender_material) != base_before  # viewport material refreshed


def test_construction_resend_repaints_displayed_style(tmp_path):
    """Re-sending a construction with a changed palette colour reuses and
    recolours the IFC style; the already-linked Blender material (viewport
    diffuse_color and Principled Base Color) must follow."""
    import bpy

    server = None
    old_cache_env = None
    try:
        server, cache_dir, info, old_cache_env = _seeded_server(tmp_path)
        port, token = info["port"], info["token"]
        client = ListenerClient()
        client.register(bonsai_addon._model_path())

        status, builtin = _request(port, token, "GET", "/api/schemes/Lesosai")
        assert status == 200, builtin
        palette = {name: dict(style) for name, style in builtin["categories"].items()}

        def set_concrete(rgb):
            palette["Concrete"] = {"hatch": "", "color": list(rgb)}
            status, body = _request(port, token, "POST", "/api/schemes/Mine", {"categories": palette})
            assert status == 200, body
            status, body = _request(port, token, "POST", "/api/config", {"scheme": "Mine"})
            assert status == 200, body

        def send(construction):
            status, body = _request(
                port,
                token,
                "POST",
                "/api/listener/send",
                {"client_id": client.client_id, "action": "add_construction", "construction": construction},
            )
            assert status == 200, body

        def drain():
            previous = bonsai_addon._CLIENT
            bonsai_addon._CLIENT = client
            try:
                assert bonsai_addon._poll_timer() == 1.0
            finally:
                bonsai_addon._CLIENT = previous

        construction = {
            "name": "Mur b\u00e9ton",
            "design_usage": "consDesignForWall",
            "layers": [{"material_id": "00000000-0000-0000-0000-000000000002", "thickness_m": 0.15}],
        }
        set_concrete((7, 8, 9))
        send(construction)
        drain()

        file = tool.Ifc.get()
        style = next(s for s in file.by_type("IfcSurfaceStyle") if s.Name == "category Concrete")
        blender_material = tool.Ifc.get_object(style)
        assert isinstance(blender_material, bpy.types.Material)
        assert tuple(round(c * 255) for c in blender_material.diffuse_color[:3]) == (7, 8, 9)

        set_concrete((10, 20, 30))
        send(construction)
        drain()

        colour = next(s for s in style.Styles if s.is_a("IfcSurfaceStyleShading")).SurfaceColour
        assert (round(colour.Red * 255), round(colour.Green * 255), round(colour.Blue * 255)) == (10, 20, 30)
        assert tool.Ifc.get_object(style) is blender_material
        assert tuple(round(c * 255) for c in blender_material.diffuse_color[:3]) == (10, 20, 30)
        assert tuple(round(c * 255) for c in _base_colour(blender_material)[:3]) == (10, 20, 30)
    finally:
        if server is not None:
            _stop_server(server, old_cache_env)
