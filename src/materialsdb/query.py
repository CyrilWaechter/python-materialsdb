"""Convenience facade over the SQLite material store."""
from functools import lru_cache
from typing import List, Optional

from materialsdb.classes import Material
from materialsdb.store import MaterialStore, Report
from materialsdb.summary import MaterialSummary


@lru_cache(maxsize=1)
def get_store() -> MaterialStore:
    return MaterialStore()


def get_material(material_id: str) -> Optional[Material]:
    return get_store().get(material_id)


def search(text: str, **filters) -> List[MaterialSummary]:
    return get_store().summaries(text=text, **filters)


def refresh(force: bool = False) -> Report:
    return get_store().refresh(force=force)
