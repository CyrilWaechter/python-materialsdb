"""Headless smoke test for the constructions page JavaScript.

Runs the real static/app-constructions.js inside a Node VM with DOM stubs and
asserts the add-layer flow renders material rows between the Rsi/Rse boundary
rows. Regression guard: an innerHTML/appendChild mix used to erase rows.

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


def test_construction_page_add_layer_renders_rows():
    root = Path(__file__).parent.parent
    harness = Path(__file__).parent / "harness" / "construction_page_harness.mjs"
    app_js = root / "src" / "materialsdb" / "gui" / "static" / "app-constructions.js"

    result = subprocess.run(
        [_node(), str(harness), str(app_js)],
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=60,
        check=False,
    )
    assert "BUG REPRODUCED" not in result.stdout + result.stderr, result.stdout
    assert result.returncode == 0, result.stdout + result.stderr
    assert "data row present: true" in result.stdout
