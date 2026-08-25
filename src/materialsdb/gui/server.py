"""Local web UI + HTTP API for exploring and exporting materialsdb materials."""

import http.server
import json
import secrets
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from materialsdb import config, utils

STATIC_DIR = Path(__file__).with_name("static")


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GuiState:
    def __init__(self, store=None):
        self.token = secrets.token_urlsafe(16)
        self.store = store
        self.session_path = None
        self.file = None

    def resolve_store(self):
        if self.store is not None:
            return self.store
        from materialsdb import query

        return query.get_store()


def detail_payload(store_, material_id):
    from materialsdb.classes import Material  # noqa: F401 - typing aid only

    summary = store_.get_summary(material_id)
    if summary is None:
        return None
    material = store_.get(material_id)
    payload = asdict(summary)
    country = config.get_country()
    if summary.type == "btk":
        u_values = []
        variations = getattr(getattr(material, "variations", None), "variation", ()) or ()
        for variation in variations:
            thermal = utils.get_by_country(variation.vthermal or (), country)
            if thermal is not None and thermal.U_value_without is not None:
                u_values.append(thermal.U_value_without)
        payload["u_value_without"] = [min(u_values), max(u_values)] if u_values else None
        payload.pop("lambda_min", None)
        payload.pop("lambda_max", None)
    elif summary.type == "construction":
        construction = getattr(material, "construction", None)
        payload["consref"] = str(getattr(construction, "consref", "") or "")
        payload["designusage"] = str(getattr(construction, "designusage", "") or "")
    return payload


class GuiHandler(http.server.BaseHTTPRequestHandler):
    state: GuiState
    server_version = "materialsdb-gui"

    def log_message(self, format, *args):
        pass

    def _send(self, status, payload=None, content_type="application/json", raw=None):
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        return self.headers.get("X-MaterialsDB-Token") == self.state.token

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def do_GET(self):
        parsed = urlparse(self.path)
        store_ = self.state.resolve_store()
        if parsed.path == "/":
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", self.state.token)
            self._send(200, content_type="text/html; charset=utf-8", raw=html.encode("utf-8"))
            return
        if parsed.path == "/app.js":
            js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
            self._send(200, content_type="text/javascript; charset=utf-8", raw=js.encode("utf-8"))
            return
        if parsed.path == "/api/materials":
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            rows = store_.summaries(
                company=params.get("company") or None,
                category=params.get("category") or None,
                type=params.get("type") or None,
                min_lambda=_float(params.get("min_lambda")),
                max_lambda=_float(params.get("max_lambda")),
                min_thick=_float(params.get("min_thick")),
                max_thick=_float(params.get("max_thick")),
                usage=params.get("usage") or None,
                text=params.get("text") or None,
                sort=params.get("sort") or "company",
                ascending=params.get("order") != "desc",
                lang=params.get("lang") or None,
            )
            self._send(200, {"materials": [asdict(row) for row in rows]})
            return
        if parsed.path.startswith("/api/materials/"):
            material_id = parsed.path.rsplit("/", 1)[1]
            payload = detail_payload(store_, material_id)
            if payload is None:
                self._send(404, {"error": f"Unknown material id: {material_id}"})
            else:
                self._send(200, payload)
            return
        self._send(404, {"error": "not found"})


class GuiServer(http.server.ThreadingHTTPServer):
    gui_state: GuiState


def make_server(state=None, port=0):
    state = state or GuiState()
    handler = type("BoundGuiHandler", (GuiHandler,), {"state": state})
    server = GuiServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    server.gui_state = state
    return server
