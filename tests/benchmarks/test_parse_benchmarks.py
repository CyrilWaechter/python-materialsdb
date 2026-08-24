from pathlib import Path

import pytest

from materialsdb import cache
from materialsdb.serialiser import XmlDeserialiser

MINI_XML = Path(__file__).parents[1] / "fixtures" / "mini_producer.xml"
DESERIALISER = XmlDeserialiser()

pytestmark = pytest.mark.benchmark


def test_parse_mini_fixture(benchmark):
    benchmark(DESERIALISER.from_xml, str(MINI_XML))


@pytest.mark.skipif(
    not next(cache.producers(), None),
    reason="no local producers cache",
)
def test_parse_all_cached_producers(benchmark):
    def parse_all():
        for producer in cache.producers():
            DESERIALISER.from_xml(str(producer))

    benchmark(parse_all)


@pytest.mark.skipif(
    next(cache.producers(), None) is None,
    reason="no local producers cache",
)
def test_parse_largest_cached_producer(benchmark):
    largest = max(cache.producers(), key=lambda p: p.stat().st_size)
    benchmark(DESERIALISER.from_xml, str(largest))
