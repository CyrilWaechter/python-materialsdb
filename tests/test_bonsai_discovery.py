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
