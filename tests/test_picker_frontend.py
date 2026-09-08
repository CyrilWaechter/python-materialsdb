"""Headless test for the picker's shared selection-union helper.

Runs the real static/picker-core.js inside Node and asserts collectItems
merges whole-material picks with layer-only picks — regression guard: the
send/append flows iterated `selected` only, silently dropping layer-only
picks.

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


def test_picker_core_collect_items_union():
    root = Path(__file__).parent.parent
    harness = Path(__file__).parent / "harness" / "picker_core_harness.mjs"
    core_js = root / "src" / "materialsdb" / "gui" / "static" / "picker-core.js"

    result = subprocess.run(
        [_node(), str(harness), str(core_js)],
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PICKER-CORE OK" in result.stdout
