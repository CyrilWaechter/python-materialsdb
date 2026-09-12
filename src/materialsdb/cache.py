import os
import pathlib
import urllib.request
from collections import namedtuple

from lxml import etree

MATERIALSDBINDEXURLLIST = [
    "http://www.materialsdb.org/download/ProducerIndex.xml",
    "http://www.materialsdb.org/download/generic/GenericIndex.xml",
]


def get_cache_folder():
    cache_dir = pathlib.Path(
        os.environ.get("APPDATA") or os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
    ).joinpath(
        "materialsdb",
    )
    pathlib.Path(cache_dir).mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cached_index_path(index) -> pathlib.Path:
    return get_cache_folder() / pathlib.Path(index).name


def parse_cached_index(index) -> etree._ElementTree:
    path = get_cached_index_path(index)
    if path.exists():
        return etree.parse(str(path))
    root = etree.Element("root")
    return etree.ElementTree(root)


def get_by_id(root: etree._Element, id: str) -> etree._Element | None:
    for company in root:
        if company.get("id") == id:
            return company
    return None


def require_update(cached_company, company) -> bool:
    if cached_company is None:
        return True
    for attrib in ["LastKnownDate", "KnownVersion"]:
        if company.get(attrib) > cached_company.get(attrib):
            return True
    return False


def get_producers_dir() -> pathlib.Path:
    producer_path = get_cache_folder().joinpath("Producers")
    pathlib.Path(producer_path).mkdir(parents=True, exist_ok=True)
    return producer_path


Report = namedtuple("Report", ["existing", "updated", "deleted"])


class _IndexPlan:
    def __init__(self, index, new_index, unchanged, todos):
        self.index = index
        self.new_index = new_index
        self.unchanged = unchanged
        self.todos = todos  # list[(company, cached_producer|None, producer_path)]


def _plan_index(index):
    cached_index = parse_cached_index(index)
    cached_root = cached_index.getroot()
    with urllib.request.urlopen(index) as response:
        new_index = etree.parse(response)
    new_root = new_index.getroot()
    producers_dir = get_producers_dir()
    unchanged = []
    todos = []
    for company in new_root:
        cached_producer = get_by_id(cached_root, company.get("id"))
        producer_path = producers_dir / pathlib.Path(company.get("href")).name
        if not require_update(cached_producer, company) and producer_path.exists():
            unchanged.append(producer_path)
            continue
        todos.append((company, cached_producer, producer_path))
    return _IndexPlan(index, new_index, unchanged, todos)


def update_producers_data(url_list=MATERIALSDBINDEXURLLIST, on_progress=None):
    """on_progress(done, total, name) is called after each producer download
    with a global running total; returning a truthy value stops the update
    between two downloads (the partial Report is returned as-is)."""
    plans = [_plan_index(index) for index in url_list]
    total = sum(len(plan.todos) for plan in plans)
    existing, updated, deleted = [], [], []
    done = 0
    for plan in plans:
        for company, cached_producer, producer_path in plan.todos:
            if cached_producer is not None:
                cached_path = get_producers_dir() / pathlib.Path(cached_producer.get("href")).name
                deleted.append(cached_path)
                cached_path.unlink(True)
            urllib.request.urlretrieve(company.get("href"), producer_path)
            updated.append(producer_path)
            done += 1
            if on_progress is not None and on_progress(done, total, pathlib.Path(company.get("href")).name):
                return Report(existing, updated, deleted)
        if plan.todos:
            plan.new_index.write(str(get_cached_index_path(plan.index)))
        existing.extend(plan.unchanged)
    return Report(existing, updated, deleted)


def update_producers_from_index(index):
    """Legacy single-index entry point (no progress callback)."""
    return update_producers_data([index])


def updates_available(url_list=MATERIALSDBINDEXURLLIST) -> bool:
    """True when any remote company differs from the cached index."""
    for index in url_list:
        cached_root = parse_cached_index(index).getroot()
        with urllib.request.urlopen(index) as response:
            new_root = etree.parse(response).getroot()
        for company in new_root:
            if require_update(get_by_id(cached_root, company.get("id")), company):
                return True
    return False


def producers():
    for producer in get_producers_dir().iterdir():
        if producer.suffix.lower() == ".xml":
            yield producer


def main():
    update_producers_data()


if __name__ == "__main__":
    main()
