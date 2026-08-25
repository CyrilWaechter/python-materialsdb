"""Local web UI + HTTP API for exploring and exporting materialsdb materials."""

import http.server
import json
import secrets
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from materialsdb import config, utils
from materialsdb.ifc.material_builder import add_material

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
            try:
                html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            except FileNotFoundError:
                self._send(404, {"error": "not found"})
                return
            html = html.replace("__TOKEN__", self.state.token)
            self._send(200, content_type="text/html; charset=utf-8", raw=html.encode("utf-8"))
            return
        if parsed.path == "/app.js":
            try:
                js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
            except FileNotFoundError:
                self._send(404, {"error": "not found"})
                return
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

    def do_POST(self):
        parsed = urlparse(self.path)
        if not self._authorized():
            self._send(403, {"error": "forbidden"})
            return
        payload = self._read_json()
        store_ = self.state.resolve_store()
        try:
            if parsed.path == "/api/export":
                self._export(store_, payload)
            elif parsed.path == "/api/session/open":
                self._session_open(payload)
            elif parsed.path == "/api/pick":
                self._pick(store_, payload)
            elif parsed.path == "/api/session/save":
                self._session_save(payload)
            elif parsed.path == "/api/config":
                self._config(payload)
            elif parsed.path == "/api/refresh":
                self._refresh(payload)
            else:
                self._send(404, {"error": "not found"})
        except Exception as err:  # noqa: BLE001 - one bad request must not kill the server
            self._send(500, {"error": str(err)})

    def _export(self, store_, payload):
        import tempfile
        import uuid

        from materialsdb import utils
        from materialsdb.ifc.project_library import ProjectLibrary

        ids = payload.get("ids") or []
        if not ids:
            self._send(400, {"error": "ids required"})
            return
        library = ProjectLibrary()
        library.create_project_library(
            company="MaterialsDB Export",
            companyid=str(uuid.uuid4()),
            ver=1,
            crd=utils.new_tdatetime(),
        )
        added = []
        for material_id in ids:
            summary = store_.get_summary(material_id)
            material = store_.get(material_id)
            if summary is None or material is None:
                continue
            add_material(library.file, material, company_id=summary.company_id or "", company=summary.company)
            added.append(material_id)
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as handle:
            temp_path = handle.name
        library.file.write(temp_path)
        data = Path(temp_path).read_bytes()
        Path(temp_path).unlink(missing_ok=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/ifc")
        self.send_header("Content-Disposition", 'attachment; filename="materialsdb_export.ifc"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _session_open(self, payload):
        import ifcopenshell

        path = payload.get("path")
        candidate = Path(path) if path else None
        if candidate is None or not candidate.exists():
            self._send(400, {"error": f"path does not exist: {path}"})
            return
        try:
            self.state.file = ifcopenshell.open(str(candidate))
        except Exception as err:  # noqa: BLE001 - malformed ifc must surface as a 400, not a crash
            self._send(400, {"error": f"could not open ifc: {err}"})
            return
        self.state.session_path = candidate
        self._send(200, {"path": str(candidate)})

    def _pick(self, store_, payload):
        if self.state.file is None:
            self._send(409, {"error": "no session open"})
            return
        ids = payload.get("ids") or []
        if not ids:
            self._send(400, {"error": "ids required"})
            return
        replace = bool(payload.get("replace"))
        added = 0
        missing = []
        for material_id in ids:
            summary = store_.get_summary(material_id)
            material = store_.get(material_id)
            if summary is None or material is None:
                missing.append(material_id)
                continue
            add_material(
                self.state.file,
                material,
                company_id=summary.company_id or "",
                company=summary.company,
                replace=replace,
            )
            added += 1
        self._send(200, {"added": added, "missing": missing})

    def _session_save(self, payload):
        if self.state.file is None:
            self._send(409, {"error": "no session open"})
            return
        destination = Path(payload.get("path") or self.state.session_path)  # ty: ignore[invalid-argument-type]
        self.state.file.write(str(destination))
        self._send(200, {"saved": str(destination)})

    def _config(self, payload):
        from materialsdb import config

        lang = payload.get("lang")
        country = payload.get("country")
        if lang:
            config.set_lang(str(lang).lower())
        if country:
            config.set_country(str(country).upper())
        self._send(200, {"ok": True})

    def _refresh(self, payload):
        from materialsdb import query

        report = query.refresh(force=bool(payload.get("force")))
        self._send(
            200,
            {
                "existing": len(report.existing),
                "updated": [str(p) for p in report.updated],
                "deleted": [str(p) for p in report.deleted],
                "skipped": [str(p) for p in report.skipped],
            },
        )


class GuiServer(http.server.ThreadingHTTPServer):
    gui_state: GuiState


def make_server(state=None, port=0):
    state = state or GuiState()
    handler = type("BoundGuiHandler", (GuiHandler,), {"state": state})
    server = GuiServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    server.gui_state = state
    return server
