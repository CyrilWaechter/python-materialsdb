import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev_utils"))

import build_bonsai_addon  # ty: ignore[unresolved-import] - dev_util on a side path


def test_default_build_unchanged(tmp_path):
    out = build_bonsai_addon.build(out_dir=tmp_path)

    assert out.name == "materialsdb_listener.zip"
    with zipfile.ZipFile(out) as zf:
        assert "blender_manifest.toml" in zf.namelist()
        assert "blender_manifest.toml".replace(".", "/") not in zf.namelist()  # manifest at zip ROOT


def test_version_injected_into_manifest_and_name(tmp_path):
    out = build_bonsai_addon.build(version="1.2.3", out_dir=tmp_path)

    assert out.name == "materialsdb_listener-1.2.3.zip"
    with zipfile.ZipFile(out) as zf:
        manifest = zf.read("blender_manifest.toml").decode("utf-8")
        assert 'version = "1.2.3"' in manifest
