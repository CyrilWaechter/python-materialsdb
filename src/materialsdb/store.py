import datetime
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from lxml import etree, objectify

from materialsdb import cache, config
from materialsdb.classes import Material
from materialsdb.serialiser import XmlDeserialiser, get_valid_root
from materialsdb.summary import MaterialSummary, summarize_material

Report = cache.Report

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id TEXT PRIMARY KEY, company_id TEXT, company TEXT, category TEXT,
    names TEXT, descriptions TEXT,
    lambda_min REAL, lambda_max REAL, thick_min REAL, thick_max REAL,
    usage TEXT, source_file TEXT, xml BLOB);
CREATE INDEX IF NOT EXISTS idx_company ON materials(company);
CREATE INDEX IF NOT EXISTS idx_category ON materials(category);
CREATE INDEX IF NOT EXISTS idx_lambda ON materials(lambda_min);
CREATE TABLE IF NOT EXISTS producer_files (
    path TEXT PRIMARY KEY, sha256 TEXT, built_at REAL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_NUMERIC_SORTS = {"lambda": "lambda_min", "thick": "thick_min"}
_STRING_SORTS = {"company": "company", "category": "category"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MaterialStore:
    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = (
            Path(db_path) if db_path else cache.get_cache_folder() / "materials.db"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.db_path))
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(_SCHEMA)
        self._ensure_schema_version()

    # ---------- meta / lifecycle ----------

    def _ensure_schema_version(self):
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        stored = row[0] if row else None
        if stored != SCHEMA_VERSION:
            self.connection.execute("DELETE FROM materials")
            self.connection.execute("DELETE FROM producer_files")
            self.connection.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )
            self.connection.commit()

    def close(self):
        self.connection.close()

    # ---------- build / refresh ----------

    def refresh(self, force=False, paths=None) -> Report:
        if paths is None:
            paths = list(cache.producers())
        else:
            paths = [Path(p) for p in paths]

        kept = {str(p) for p in paths}
        deleted = []
        for (stored_path,) in self.connection.execute(
            "SELECT path FROM producer_files"
        ).fetchall():
            if stored_path not in kept:
                self.connection.execute(
                    "DELETE FROM materials WHERE source_file=?", (stored_path,)
                )
                self.connection.execute(
                    "DELETE FROM producer_files WHERE path=?", (stored_path,)
                )
                deleted.append(Path(stored_path))

        existing, updated = [], []
        deserialiser = XmlDeserialiser()
        for path in paths:
            digest = _sha256(path)
            row = self.connection.execute(
                "SELECT sha256 FROM producer_files WHERE path=?", (str(path),)
            ).fetchone()
            if not force and row and row[0] == digest:
                existing.append(path)
                continue
            try:
                self._upsert_file(deserialiser, path)
            except Exception as err:
                print(f"{path.name}: skipped during store refresh:\n\t{err}")
                continue
            self.connection.execute(
                "INSERT INTO producer_files(path, sha256, built_at) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, "
                "built_at=excluded.built_at",
                (str(path), digest, datetime.datetime.now().timestamp()),
            )
            updated.append(path)

        self.connection.commit()
        return Report(existing, updated, deleted)

    def _upsert_file(self, deserialiser: XmlDeserialiser, path: Path):
        tree = objectify.parse(str(path))
        root = get_valid_root(tree)
        source = deserialiser.from_element(root)
        self.connection.execute(
            "DELETE FROM materials WHERE source_file=?", (str(path),)
        )
        for element in root.material:
            material = deserialiser.from_element(element)
            summary = summarize_material(
                material, company_id=str(source.companyid), company=source.company
            )
            self.connection.execute(
                "INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    summary.id,
                    summary.company_id,
                    summary.company,
                    summary.category,
                    json.dumps(summary.names),
                    json.dumps(summary.descriptions),
                    summary.lambda_min,
                    summary.lambda_max,
                    summary.thick_min,
                    summary.thick_max,
                    json.dumps(summary.usage),
                    str(path),
                    sqlite3.Binary(etree.tostring(element)),
                ),
            )

    # ---------- queries ----------

    def summaries(
        self,
        company=None,
        category=None,
        min_lambda=None,
        max_lambda=None,
        min_thick=None,
        max_thick=None,
        usage=None,
        text=None,
        sort="company",
        ascending=True,
        lang=None,
    ) -> List[MaterialSummary]:
        where, params = [], []

        def add(condition, value):
            where.append(condition)
            params.append(value)

        if company:
            add("company=?", company)
        if category:
            add("category=?", category)
        if min_lambda is not None:
            add("lambda_max>=?", min_lambda)
        if max_lambda is not None:
            add("lambda_min<=?", max_lambda)
        if min_thick is not None:
            add("thick_max>=?", min_thick)
        if max_thick is not None:
            add("thick_min<=?", max_thick)

        query = "SELECT * FROM materials"
        if where:
            query += " WHERE " + " AND ".join(where)
        rows = self.connection.execute(query, params).fetchall()

        lang = lang or config.get_lang()
        results = [self._row_to_summary(row) for row in rows]

        if usage:
            results = [r for r in results if r.usage.get(usage)]
        if text:
            needle = text.lower()
            results = [
                r
                for r in results
                if needle in (r.names.get(lang) or r.names.get("") or "").lower()
            ]
        return self._sorted(results, sort, ascending)

    @staticmethod
    def _sorted(
        results: List[MaterialSummary], sort: str, ascending: bool
    ) -> List[MaterialSummary]:
        reverse = not ascending
        if sort == "name":
            return sorted(
                results,
                key=lambda r: r.names.get("") or "",
                reverse=reverse,
            )
        if sort in _NUMERIC_SORTS:
            attr = _NUMERIC_SORTS[sort]
            return sorted(
                results,
                key=lambda r: (
                    getattr(r, attr) is None,
                    getattr(r, attr) if getattr(r, attr) is not None else 0,
                ),
                reverse=reverse,
            )
        attr = _STRING_SORTS.get(sort, "company")
        return sorted(results, key=lambda r: str(getattr(r, attr)), reverse=reverse)

    @staticmethod
    def _row_to_summary(row) -> MaterialSummary:
        (
            id_,
            company_id,
            company,
            category,
            names,
            descriptions,
            lambda_min,
            lambda_max,
            thick_min,
            thick_max,
            usage,
            _source_file,
            _xml,
        ) = row
        return MaterialSummary(
            id=id_,
            company_id=company_id,
            company=company,
            category=category,
            names=json.loads(names),
            descriptions=json.loads(descriptions),
            lambda_min=lambda_min,
            lambda_max=lambda_max,
            thick_min=thick_min,
            thick_max=thick_max,
            usage=json.loads(usage),
        )

    def get(self, material_id: str) -> Optional[Material]:
        row = self.connection.execute(
            "SELECT xml FROM materials WHERE id=?", (material_id,)
        ).fetchone()
        if row is None:
            return None
        element = objectify.fromstring(bytes(row[0]))
        return XmlDeserialiser().from_element(element)
