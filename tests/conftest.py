import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))

import pytest

from materialsdb.serialiser import XmlDeserialiser

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep builders off the developer's real preferences: a saved scheme or
    producer-colour override must not change unrelated test outcomes. Fixtures
    in test modules that patch get_config_dir later run after this one and
    keep control of the file they write."""
    monkeypatch.setattr("materialsdb.config.get_config_dir", lambda: tmp_path)


@pytest.fixture
def mini_xml() -> Path:
    return FIXTURES / "mini_producer.xml"


@pytest.fixture
def mini_source(mini_xml):
    return XmlDeserialiser().from_xml(str(mini_xml))


@pytest.fixture
def mixed_xml() -> Path:
    return FIXTURES / "mixed_types.xml"


@pytest.fixture
def mixed_source(mixed_xml):
    return XmlDeserialiser().from_xml(str(mixed_xml))
