from pathlib import Path

import pytest

from materialsdb.ifc import project_library


@pytest.mark.skipif(
    not Path("example_v103.xml").exists(),
    reason="example_v103.xml not present locally",
)
def test_create_project_library():
    file = project_library.create_project_library_from_xml("example_v103.xml")
    file.write("example_v103.ifc")
