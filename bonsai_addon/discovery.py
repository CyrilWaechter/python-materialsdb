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
        self._request(
            "POST", "/api/listener/register", {"client_id": self.client_id, "model_path": str(model_path or "")}
        )
        self.registered_path = str(model_path or "")

    def poll(self):
        return self._request("GET", f"/api/listener/poll?client_id={self.client_id}")

    def report(self, status, detail=""):
        self._request(
            "POST", "/api/listener/status", {"client_id": self.client_id, "status": status, "detail": str(detail or "")}
        )
