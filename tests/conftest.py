import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))

import pytest

from materialsdb.serialiser import XmlDeserialiser

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mini_xml() -> Path:
    return FIXTURES / "mini_producer.xml"


@pytest.fixture
def mini_source(mini_xml):
    return XmlDeserialiser().from_xml(str(mini_xml))
