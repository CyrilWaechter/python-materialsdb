"""The add-on push safety-net reporting (no bpy: `_safety_net` is pure).

`bonsai_addon/__init__.py` imports bpy/bonsai at module load, so the function
is extracted from its AST exactly as the production module defines it."""

import ast
from pathlib import Path

import pytest

ADDON_INIT = Path(__file__).resolve().parent.parent / "bonsai_addon" / "__init__.py"


@pytest.fixture(scope="module")
def safety_net():
    tree = ast.parse(ADDON_INIT.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_safety_net")
    namespace: dict = {}
    exec(  # noqa: S102 - executing our own extracted source
        compile(ast.Module(body=[function], type_ignores=[]), str(ADDON_INIT), "exec"),
        namespace,
    )
    return namespace["_safety_net"]


def test_safety_net_empty_summary(safety_net):
    assert safety_net("2 material(s) added", {}) == "2 material(s) added"


def test_safety_net_reports_changed_skipped_and_replace_failed(safety_net):
    report = safety_net("2 material(s) added", {"changed_skipped": ["m1"], "replace_failed": [{"material_id": "m2"}]})
    assert report == "2 material(s) added; 1 changed material(s) left unchanged; 1 replacement(s) failed"


def test_safety_net_reports_update_missing(safety_net):
    report = safety_net("2 material(s) added", {"update_missing": ["layer-a", "layer-b"]})
    assert report == "2 material(s) added; 2 update target(s) not found in the model"


def test_safety_net_reports_all_three(safety_net):
    report = safety_net(
        "1 type(s) created",
        {"changed_skipped": ["m1"], "replace_failed": [{"material_id": "m2"}], "update_missing": ["layer-a"]},
    )
    assert report == (
        "1 type(s) created; 1 changed material(s) left unchanged; "
        "1 replacement(s) failed; 1 update target(s) not found in the model"
    )
