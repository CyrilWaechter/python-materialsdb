"""Create a standalone IFC file for a single materialsdb material.

Usage:
    python examples/create_single_material_ifc.py <material_id> [output.ifc]
"""

import sys

from materialsdb import config, query
from materialsdb.ifc.material_builder import create_material_file


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    material_id = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else f"{material_id}.ifc"

    query.refresh()  # incremental update from cached producer xml
    config.set_lang("en")
    config.set_country("CH")
    file = create_material_file(material_id)
    file.write(output)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
