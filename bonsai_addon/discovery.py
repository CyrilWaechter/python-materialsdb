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


def clear_gui_info():
    """Remove a (possibly stale) discovery file; the server rewrites one on
    start, and a dead server's leftover would point clients at a dead port."""
    (_cache_folder() / "gui.json").unlink(missing_ok=True)


USED_CLASSES = ("IfcWall", "IfcSlab", "IfcRoof", "IfcWallType", "IfcSlabType", "IfcRoofType")


def used_materials_by_class(file):
    """IfcMaterial entity id -> sorted class names using it (walls/slabs/roofs).

    Relationship-driven: an element/type counts when it is a RelatedObject of an
    IfcRelAssociatesMaterial. Covers occurrences and types, so materials
    inherited from a type are included."""
    used = {}
    for rel in file.by_type("IfcRelAssociatesMaterial"):
        classes = sorted({obj.is_a() for obj in rel.RelatedObjects or () if obj.is_a() in USED_CLASSES})
        if not classes:
            continue
        material = rel.RelatingMaterial
        if material is None:
            continue
        while material.is_a("IfcMaterialLayerSetUsage"):
            material = material.ForLayerSet
        materials = []
        if material.is_a("IfcMaterialLayerSet"):
            materials = [layer.Material for layer in material.MaterialLayers or () if layer.Material is not None]
        elif material.is_a("IfcMaterial"):
            materials = [material]
        for entity in materials:
            used.setdefault(entity.id(), set()).update(classes)
    return {entity_id: sorted(classes) for entity_id, classes in used.items()}


def _model_material_map(file):
    """material_id -> {"fingerprint", "scheme", "used_in", "layers"}.

    ``layers`` holds one entry per model IfcMaterial:
    ``{"entity_id", "layer_id", "thick"}`` — ``entity_id`` is the STEP id (a
    stable key even for legacy materials with no `materialsdb.org_layer`),
    ``layer_id`` the org_layer GUID when present, and ``thick`` the org_layer
    thickness (metres), else the material's IfcMaterialLayer thickness."""
    import ifcopenshell.util.element

    thickness_by_material = {}
    for layer in file.by_type("IfcMaterialLayer"):
        if layer.Material is not None and layer.Material not in thickness_by_material:
            thickness_by_material[layer.Material] = layer.LayerThickness

    used = used_materials_by_class(file)
    materials = {}
    for material in file.by_type("IfcMaterial"):
        psets = ifcopenshell.util.element.get_psets(material)
        identity = psets.get("materialsdb") or {}
        material_id = identity.get("material_id")
        if material_id is None:
            continue
        entry = materials.setdefault(material_id, {"fingerprint": None, "scheme": None, "used_in": [], "layers": []})
        if identity.get("fingerprint") and entry["fingerprint"] is None:
            entry["fingerprint"] = identity["fingerprint"]
        if identity.get("fingerprint_scheme") and entry["scheme"] is None:
            entry["scheme"] = identity["fingerprint_scheme"]
        if material.id() in used:
            entry["used_in"] = sorted(set(entry["used_in"]) | set(used[material.id()]))
        org_layer = psets.get("materialsdb.org_layer") or {}
        layer_id = org_layer.get("layer_id")
        thick = org_layer.get("thick")
        if thick is None:
            thick = thickness_by_material.get(material)
        entry["layers"].append(
            {
                "entity_id": str(material.id()),
                "layer_id": str(layer_id) if layer_id is not None else None,
                "thick": thick,
            }
        )
    return materials


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
        if response.status == 404:
            self.registered_path = None  # server restarted/lost us: re-register on next tick
        if response.status != 200:
            raise RuntimeError(f"{method} {path} -> {response.status}: {data[:200]!r}")
        return json.loads(data) if data else None

    def register(self, model_path=""):
        self._request(
            "POST", "/api/listener/register", {"client_id": self.client_id, "model_path": str(model_path or "")}
        )
        self.registered_path = str(model_path or "")

    def poll(self):
        body = self._request("GET", f"/api/listener/poll?client_id={self.client_id}")
        # the server wraps pushes in {"payload": ...}; callers want the push itself
        return (body or {}).get("payload")

    def report(self, status, detail=""):
        self._request(
            "POST", "/api/listener/status", {"client_id": self.client_id, "status": status, "detail": str(detail or "")}
        )

    def send_model_map(self, model_path, materials):
        """Report the open model's material map to the GUI.

        The server ignores the top-level `scheme`; it is derived from the first
        entry that carries one (each per-material entry has its own)."""
        scheme = ""
        for entry in (materials or {}).values():
            if isinstance(entry, dict) and entry.get("scheme"):
                scheme = entry["scheme"]
                break
        self._request(
            "POST",
            "/api/listener/model",
            {
                "client_id": self.client_id,
                "model_path": str(model_path or ""),
                "scheme": scheme,
                "materials": materials,
            },
        )

    def send_to_composer(self, construction):
        self._request(
            "POST",
            "/api/composer/push",
            {
                "name": construction["name"],
                "design_usage": construction.get("design_usage"),
                "layers": construction["layers"],
            },
        )
