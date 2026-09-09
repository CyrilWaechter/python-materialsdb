"""Zip bonsai_addon/ as a Blender extension for 'Install from Disk'.

The manifest must sit at the zip root, so archive names are relative to
bonsai_addon/ itself. CI passes --version on releases: the manifest's
version is rewritten for the zipped copy and the zip gains a versioned
name (Blender's update flow compares manifest versions)."""

import argparse
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bonsai_addon"


def build(version: str | None = None, out_dir: Path | None = None) -> Path:
    out_dir = Path(out_dir) if out_dir else ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    name = f"materialsdb_listener-{version}.zip" if version else "materialsdb_listener.zip"
    out = out_dir / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(SRC.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                if version and file.name == "blender_manifest.toml":
                    manifest, replaced = re.subn(
                        r'(?m)^version = "[^"]*"$',
                        f'version = "{version}"',
                        file.read_text("utf-8"),
                        count=1,
                    )
                    if replaced != 1:
                        raise RuntimeError(f"could not inject version into {file}: no 'version = …' line")
                    zf.writestr(file.relative_to(SRC).as_posix(), manifest)
                else:
                    zf.write(file, file.relative_to(SRC))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", default=None, help="extension version; injected into the manifest and the zip name"
    )
    args = parser.parse_args()
    print(build(version=args.version))


if __name__ == "__main__":
    main()
