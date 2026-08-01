"""Persistent SQLite storage for symbol references."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterable, Iterator, Mapping

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.path_normalizer import normalize_project_root, project_path

from .symbol_reference_parser import SymbolReferenceRecord
from .stats_mapping import RevisionStats


class SymbolReferenceDatabase:
    SCHEMA_VERSION = 1

    def __init__(self, project_root: str | Path, directory: str | Path | None = None) -> None:
        self.project_root = normalize_project_root(project_root)
        normalized = os.path.normcase(str(self.project_root)).encode("utf-8", errors="replace")
        digest = hashlib.sha256(normalized).hexdigest()[:24]
        base = self._normalize_directory(directory, DATA_DIR / "symbol_reference_indexes")
        self.path = base / f"{digest}.sqlite3"
        self._lock = RLock()
        self._initialize()

    @property
    def revision(self) -> int:
        with self._lock, self._connect() as connection:
            return self._read_revision(connection)

    def replace_file(self, path: str | Path, references: Iterable[SymbolReferenceRecord]) -> bool:
        absolute = str(self._normalize_path(path))
        rows = self._rows_for_file(absolute, self._materialize_records(references))
        with self._lock, self._connect() as connection:
            current = self._select_rows_for_file(connection, absolute)
            if current == rows:
                return False
            connection.execute("DELETE FROM symbol_references WHERE path = ?", (absolute,))
            self._insert_rows(connection, rows)
            self._bump_revision(connection)
            return True

    def replace_all(self, files: Mapping[str | Path, Iterable[SymbolReferenceRecord]]) -> bool:
        if not isinstance(files, Mapping):
            raise TypeError("files must be a mapping of paths to reference iterables")
        materialized: list[tuple[object, ...]] = []
        for path, references in files.items():
            absolute = str(self._normalize_path(path))
            materialized.extend(self._rows_for_file(absolute, self._materialize_records(references)))
        rows = tuple(sorted(materialized, key=self._row_sort_key))
        with self._lock, self._connect() as connection:
            current = self._select_all_rows(connection)
            if current == rows:
                return False
            connection.execute("DELETE FROM symbol_references")
            self._insert_rows(connection, rows)
            self._bump_revision(connection)
            return True

    def remove_file(self, path: str | Path) -> bool:
        absolute = str(self._normalize_path(path))
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM symbol_references WHERE path = ?", (absolute,))
            changed = cursor.rowcount > 0
            if changed:
                self._bump_revision(connection)
            return changed

    def references_to(self, name: str, *, limit: int = 500) -> tuple[SymbolReferenceRecord, ...]:
        text = name.strip() if isinstance(name, str) else ""
        if not text:
            return ()
        bounded = _safe_limit(limit, default=500, maximum=5000)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT name, path, line, column_no, context, scope FROM symbol_references WHERE name = ? ORDER BY path, line, column_no LIMIT ?",
                (text, bounded),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def references_for_file(self, path: str | Path) -> tuple[SymbolReferenceRecord, ...]:
        absolute = str(self._normalize_path(path))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT name, path, line, column_no, context, scope FROM symbol_references WHERE path = ? ORDER BY line, column_no",
                (absolute,),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def stats(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            reference_count = int(connection.execute("SELECT COUNT(*) FROM symbol_references").fetchone()[0])
            file_count = int(connection.execute("SELECT COUNT(DISTINCT path) FROM symbol_references").fetchone()[0])
            revision = self._read_revision(connection)
        return RevisionStats(
            {
                "references": reference_count,
                "reference_files": file_count,
                "reference_revision": revision,
            }
        )

    def clear(self) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM symbol_references")
            changed = cursor.rowcount > 0
            if changed:
                self._bump_revision(connection)
            return changed

    def integrity_check(self) -> bool:
        try:
            with self._lock, self._connect() as connection:
                row = connection.execute("PRAGMA quick_check").fetchone()
            return bool(row and str(row[0]).casefold() == "ok")
        except sqlite3.DatabaseError:
            return False

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            version_row = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            try:
                schema_version = int(version_row[0]) if version_row else self.SCHEMA_VERSION
            except (TypeError, ValueError, OverflowError):
                schema_version = -1
            if schema_version != self.SCHEMA_VERSION:
                connection.execute("DROP TABLE IF EXISTS symbol_references")
            connection.execute("""CREATE TABLE IF NOT EXISTS symbol_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL,
                column_no INTEGER NOT NULL, context TEXT NOT NULL, scope TEXT)""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_symbol_refs_name ON symbol_references(name)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_symbol_refs_path ON symbol_references(path)")
            connection.execute(
                "DELETE FROM symbol_references WHERE id NOT IN (SELECT MIN(id) FROM symbol_references "
                "GROUP BY name, path, line, column_no, context, COALESCE(scope, ''))"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_symbol_refs_unique "
                "ON symbol_references(name, path, line, column_no, context, COALESCE(scope, ''))"
            )
            connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)", (str(self.SCHEMA_VERSION),))
            connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('project_root', ?)", (str(self.project_root),))
            connection.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES('revision', '0')")

    @staticmethod
    def _materialize_records(references: Iterable[SymbolReferenceRecord]) -> tuple[SymbolReferenceRecord, ...]:
        if references is None or isinstance(references, (str, bytes, bytearray, memoryview, Path)):
            raise TypeError("references must be an iterable of SymbolReferenceRecord objects")
        try:
            items = tuple(references)
        except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError) as exc:
            raise ValueError("unable to materialize references") from exc
        if any(not isinstance(item, SymbolReferenceRecord) for item in items):
            raise TypeError("references must contain only SymbolReferenceRecord objects")
        return items

    @staticmethod
    def _normalize_directory(directory: str | Path | None, default: Path) -> Path:
        value = default if directory is None else directory
        if not isinstance(value, (str, Path)):
            raise TypeError("directory must be a string or pathlib.Path")
        raw = str(value).strip()
        if not raw or "\x00" in raw:
            raise ValueError("directory cannot be empty or contain null bytes")
        path = Path(raw).expanduser().resolve(strict=False)
        if path.exists() and not path.is_dir():
            raise NotADirectoryError(str(path))
        return path

    def _normalize_path(self, path: str | Path) -> Path:
        return project_path(self.project_root, path, require_inside=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _rows_for_file(absolute: str, items: tuple[SymbolReferenceRecord, ...]) -> tuple[tuple[object, ...], ...]:
        rows = {(item.name, absolute, item.line, item.column, item.context, item.scope) for item in items}
        return tuple(sorted(rows, key=SymbolReferenceDatabase._row_sort_key))

    @staticmethod
    def _row_sort_key(row: tuple[object, ...]) -> tuple[str, int, int, str, str]:
        return (str(row[1]).casefold(), int(row[2]), int(row[3]), str(row[0]).casefold(), str(row[4]))

    @staticmethod
    def _insert_rows(connection: sqlite3.Connection, rows: Iterable[tuple[object, ...]]) -> None:
        connection.executemany(
            "INSERT OR IGNORE INTO symbol_references(name, path, line, column_no, context, scope) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )

    @staticmethod
    def _select_rows_for_file(connection: sqlite3.Connection, absolute: str) -> tuple[tuple[object, ...], ...]:
        rows = connection.execute(
            "SELECT name, path, line, column_no, context, scope FROM symbol_references WHERE path = ?", (absolute,)
        ).fetchall()
        return tuple(sorted((tuple(row) for row in rows), key=SymbolReferenceDatabase._row_sort_key))

    @staticmethod
    def _select_all_rows(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
        rows = connection.execute("SELECT name, path, line, column_no, context, scope FROM symbol_references").fetchall()
        return tuple(sorted((tuple(row) for row in rows), key=SymbolReferenceDatabase._row_sort_key))

    @staticmethod
    def _read_revision(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
        try:
            return max(0, int(row[0])) if row else 0
        except (TypeError, ValueError, OverflowError):
            return 0

    @classmethod
    def _bump_revision(cls, connection: sqlite3.Connection) -> int:
        revision = cls._read_revision(connection) + 1
        connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('revision', ?)", (str(revision),))
        return revision

    @staticmethod
    def _row_to_record(row: tuple[object, ...]) -> SymbolReferenceRecord:
        return SymbolReferenceRecord(
            name=str(row[0]), path=str(row[1]), line=int(row[2]), column=int(row[3]),
            context=str(row[4]), scope=str(row[5]) if row[5] is not None else None,
        )


def _safe_limit(value: object, *, default: int, maximum: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return default
    return max(1, min(int(numeric), maximum))
