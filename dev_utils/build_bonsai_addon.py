"""Zip bonsai_addon/ as a Blender extension for 'Install from Disk'.

The manifest must sit at the zip root, so archive names are relative to
bonsai_addon/ itself."""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bonsai_addon"
OUT = ROOT / "dist" / "materialsdb_listener.zip"


def main():
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(SRC.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                zf.write(file, file.relative_to(SRC))
    print(OUT)


if __name__ == "__main__":
    main()
