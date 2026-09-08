"""Regression tests for the add-on discovery client (no live server)."""

import http.client
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


def test_reregisters_after_server_restart(monkeypatch):
    """A 404 (stale client_id after a GUI restart) must clear registered_path
    so the next tick re-registers instead of polling a dead path forever."""
    calls = []
    responses = [(200, b""), (404, b"unknown client"), (200, b"")]

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
