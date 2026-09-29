"""Headless test for the picker's model-change UI and update/replace flow.

Runs the real static/picker-core.js + static/app.js inside a Node VM with DOM
stubs and asserts a `changed` material renders the banner count and its report
rows, and that choosing Update puts ``mode:"update"`` + the layer mapping into
the send payload.

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


def test_model_changes_banner_report_and_update_payload():
    root = Path(__file__).parent.parent
    harness = Path(__file__).parent / "harness" / "model_changes_harness.mjs"
    static = root / "src" / "materialsdb" / "gui" / "static"

    result = subprocess.run(
        [_node(), str(harness), str(static / "picker-core.js"), str(static / "app.js")],
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    assert "FAIL" not in output, output
    assert result.returncode == 0, output
    assert "MODEL-CHANGES OK" in result.stdout
