"""Headless smoke test for the settings page JavaScript.

Runs the real static/settings.js inside a Node VM with DOM stubs and asserts the
general/colour-scheme controls: ``#scheme`` options come from ``/api/schemes``
with the effective scheme from ``/api/config``, change events post the matching
config patch, and apply-colours calls ``/api/model/restyle`` and reports the
queued count plus the listener's applied detail. Without a connected model the
button is disabled and the hint is shown.

Skipped automatically when node is unavailable."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE: str | None = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _node() -> str:
    assert NODE is not None, "node not available"
    return NODE


def test_settings_page_scheme_and_apply_colours():
    root = Path(__file__).parent.parent
    harness = Path(__file__).parent / "harness" / "settings_page_harness.mjs"
    settings_js = root / "src" / "materialsdb" / "gui" / "static" / "settings.js"

    result = subprocess.run(
        [_node(), str(harness), str(settings_js)],
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "FAIL" not in output, output
    assert "scheme options: OK" in result.stdout
    assert "override post: OK" in result.stdout
    assert "scheme post: OK" in result.stdout
    assert "apply: OK" in result.stdout
    assert "no-model hint: OK" in result.stdout
