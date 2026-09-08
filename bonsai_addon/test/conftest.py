"""In-Blender test harness for the materialsdb Bonsai add-on.

Run with pytest-blender ACTIVE (never pass -p no:pytest-blender here):
    pytest bonsai_addon/test -q
Requires a local Blender with Bonsai importable (extension installed), so
these tests are local verification only — CI has no Blender.

Conftest must stay importable OUTSIDE Blender (pytest loads initial
conftests before pytest-blender can relaunch inside Blender), so bpy/bonsai
imports live inside the fixture."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))  # import bonsai_addon as a package

import pytest


@pytest.fixture(autouse=True)
def fresh_project():
    """A fresh empty IFC project per test, mirroring bonsai's
    test/bim/bootstrap.py::NewIfc."""
    import bonsai.bim.handler
    import bpy
    from bonsai.bim.ifc import IfcStore

    IfcStore.purge()
    bpy.ops.wm.read_homefile(app_template="")
    if bpy.data.objects:
        bpy.data.batch_remove(bpy.data.objects)
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    bonsai.bim.handler.load_post(None)
    bpy.ops.bim.create_project()
    yield


@pytest.fixture(scope="session", autouse=True)
def registered_addon():
    import bonsai_addon

    try:
        bonsai_addon.register()
    except Exception as err:  # noqa: BLE001 - already registered in this Blender session
        print(f"add-on register skipped: {err}")
    yield
