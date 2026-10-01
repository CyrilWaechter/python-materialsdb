"""Headless smoke test for the settings page JavaScript.

Runs the real static/settings.js inside a Node VM with DOM stubs and asserts the
general/colour-scheme controls: ``#scheme`` options come from ``/api/schemes``
with the effective scheme from ``/api/config``, change events post the matching
config patch, and apply-colours calls ``/api/model/restyle`` with the selected
``#model-target`` client_id and reports the queued count plus, after the 1 s
status poll, the listener's applied detail; the selected target survives a
refresh. Without a connected model the button is disabled, the hint is shown
and the target selector hidden. The palette
editor core is covered too: selecting a built-in loads all 17 rows as an
editable draft with Save disabled and Save as enabled (a custom draft
re-enables Save), an input event
re-renders the live preview and save-as posts the edited palette under the
encoded name. Save-as refuses an existing name with an inline conflict instead
of overwriting it, while a free name still posts. Selecting away from a dirty
draft asks first: the edited scheme itself is a no-op, cancel keeps the draft
and accept switches. Finally rename and delete post to their encoded
endpoints, export writes the current draft envelope through a stubbed
``window.showSaveFilePicker`` (with the suggested file name) while a cancelled
picker or prompt stays quiet, and importing
surfaces the 409 conflict before the retry succeeds.

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
    assert "model target: OK" in result.stdout
    assert "target selection kept: OK" in result.stdout
    assert "malformed clients tolerated: OK" in result.stdout
    assert "apply: OK" in result.stdout
    assert "restyle target: OK" in result.stdout
    assert "apply status poll: OK" in result.stdout
    assert "no-model hint: OK" in result.stdout
    assert "editor rows: OK" in result.stdout
    assert "preview: OK" in result.stdout
    assert "save payload: OK" in result.stdout
    assert "save-as conflict: OK" in result.stdout
    assert "save-as free name: OK" in result.stdout
    assert "select same keeps draft: OK" in result.stdout
    assert "select cancel keeps draft: OK" in result.stdout
    assert "select accept switches: OK" in result.stdout
    assert "custom draft buttons: OK" in result.stdout
    assert "export: OK" in result.stdout
    assert "export cancel quiet: OK" in result.stdout
    assert "export prompt cancel quiet: OK" in result.stdout
    assert "rename: OK" in result.stdout
    assert "delete: OK" in result.stdout
    assert "import conflict: OK" in result.stdout
    assert "import success: OK" in result.stdout
