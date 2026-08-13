"""Persistent SQLite storage for project symbol indexes."""
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

from .symbol_parser import SymbolRecord
from .stats_mapping import RevisionStats

from .sqlite_runtime import path_lock, transaction


class SymbolDatabase:
    SCHEMA_VERSION = 1

    def __init__(self, project_root: str | Path, directory: str | Path | None = None) -> None:
        self.project_root = normalize_project_root(project_root)
        normalized = os.path.normcase(str(self.project_root)).encode("utf-8", errors="replace")
        digest = hashlib.sha256(normalized).hexdigest()[:24]
        base = self._normalize_directory(directory, DATA_DIR / "symbol_indexes")
        self.path = base / f"{digest}.sqlite3"
        self._lock = path_lock(self.path)
        self._initialize()

    @property
    def revision(self) -> int:
        with self._lock, self._connect() as connection:
            return self._read_revision(connection)

    def replace_file(self, path: str | Path, symbols: Iterable[SymbolRecord]) -> bool:
        absolute = str(self._normalize_path(path))
        rows = self._rows_for_file(absolute, self._materialize_records(symbols))
        with self._lock, self._connect() as connection:
            current = self._select_rows_for_file(connection, absolute)
            if current == rows:
                return False
            connection.execute("DELETE FROM symbols WHERE path = ?", (absolute,))
            self._insert_rows(connection, rows)
            self._bump_revision(connection)
            return True

    def replace_all(self, files: Mapping[str | Path, Iterable[SymbolRecord]]) -> bool:
        if not isinstance(files, Mapping):
            raise TypeError("files must be a mapping of paths to symbol iterables")
        materialized: list[tuple[object, ...]] = []
        for path, symbols in files.items():
            absolute = str(self._normalize_path(path))
            materialized.extend(self._rows_for_file(absolute, self._materialize_records(symbols)))
        rows = tuple(sorted(materialized, key=self._row_sort_key))
        with self._lock, self._connect() as connection:
            current = self._select_all_rows(connection)
            if current == rows:
                return False
            connection.execute("DELETE FROM symbols")
            self._insert_rows(connection, rows)
            self._bump_revision(connection)
            return True

    def remove_file(self, path: str | Path) -> bool:
        absolute = str(self._normalize_path(path))
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM symbols WHERE path = ?", (absolute,))
            changed = cursor.rowcount > 0
            if changed:
                self._bump_revision(connection)
            return changed

    def search(self, query: str, *, kinds: Iterable[str] = (), limit: int = 100) -> tuple[SymbolRecord, ...]:
        text = query.strip() if isinstance(query, str) else ""
        if not text:
            return ()
        kind_values = self._normalize_kinds(kinds)
        sql = "SELECT name, qualified_name, kind, path, line, end_line, column_no, parent, decorators, bases, signature FROM symbols WHERE (name LIKE ? OR qualified_name LIKE ?)"
        params: list[object] = [f"%{text}%", f"%{text}%"]
        if kind_values:
            sql += f" AND kind IN ({','.join('?' for _ in kind_values)})"
            params.extend(kind_values)
        sql += " ORDER BY CASE WHEN name = ? THEN 0 WHEN name LIKE ? THEN 1 ELSE 2 END, qualified_name LIMIT ?"
        params.extend([text, f"{text}%", self._normalize_limit(limit)])
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def symbols_for_file(self, path: str | Path) -> tuple[SymbolRecord, ...]:
        absolute = str(self._normalize_path(path))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT name, qualified_name, kind, path, line, end_line, column_no, parent, decorators, bases, signature FROM symbols WHERE path = ? ORDER BY line, column_no",
                (absolute,),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def stats(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            symbol_count = int(connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0])
            file_count = int(connection.execute("SELECT COUNT(DISTINCT path) FROM symbols").fetchone()[0])
            revision = self._read_revision(connection)
        return RevisionStats(
            {"symbols": symbol_count, "files": file_count, "symbol_revision": revision}
        )

    def clear(self) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM symbols")
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
                stored_version = int(version_row[0]) if version_row else self.SCHEMA_VERSION
            except (TypeError, ValueError, OverflowError):
                stored_version = -1
            if stored_version != self.SCHEMA_VERSION:
                connection.execute("DROP TABLE IF EXISTS symbols")
            connection.execute("""CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                qualified_name TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
                line INTEGER NOT NULL, end_line INTEGER NOT NULL, column_no INTEGER NOT NULL,
                parent TEXT, decorators TEXT NOT NULL DEFAULT '', bases TEXT NOT NULL DEFAULT '',
                signature TEXT NOT NULL DEFAULT '')""")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path)")
            connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)", (str(self.SCHEMA_VERSION),))
            connection.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES('project_root', ?)", (str(self.project_root),))
            connection.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES('revision', '0')")

    @staticmethod
    def _materialize_records(symbols: Iterable[SymbolRecord]) -> tuple[SymbolRecord, ...]:
        if symbols is None or isinstance(symbols, (str, bytes, bytearray, memoryview, Path)):
            raise TypeError("symbols must be an iterable of SymbolRecord objects")
        try:
            items = tuple(symbols)
        except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError) as exc:
            raise ValueError("unable to materialize symbols") from exc
        if any(not isinstance(item, SymbolRecord) for item in items):
            raise TypeError("symbols must contain only SymbolRecord objects")
        return items

    @staticmethod
    def _normalize_directory(directory: str | Path | None, default: Path) -> Path:
        value = default if directory is None else directory
        if not isinstance(value, (str, Path)):
            raise TypeError("directory must be a string or pathlib.Path")
        raw = str(value).strip()
        if not raw or "\x00" in raw:
            raise ValueError("directory cannot be empty or contain null bytes")
        return Path(raw).expanduser().resolve(strict=False)

    @staticmethod
    def _normalize_kinds(kinds: object) -> tuple[str, ...]:
        if kinds is None:
            return ()
        values: object = (kinds,) if isinstance(kinds, str) else kinds
        if isinstance(values, (bytes, bytearray, memoryview)):
            return ()
        try:
            normalized: list[str] = []
            seen: set[str] = set()
            for item in iter(values):
                text = item.strip() if isinstance(item, str) else str(item).strip()
                if text and text not in seen:
                    seen.add(text)
                    normalized.append(text)
            return tuple(normalized)
        except (TypeError, ValueError, RuntimeError, OverflowError):
            return ()

    @staticmethod
    def _normalize_limit(limit: object) -> int:
        if isinstance(limit, bool):
            return 100
        try:
            return max(1, min(int(limit), 1000))
        except (TypeError, ValueError, OverflowError):
            return 100

    def _normalize_path(self, path: str | Path) -> Path:
        return project_path(self.project_root, path, require_inside=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with transaction(self.path, foreign_keys=True) as connection:
            yield connection

    @staticmethod
    def _rows_for_file(absolute: str, items: tuple[SymbolRecord, ...]) -> tuple[tuple[object, ...], ...]:
        rows = {
            (item.name, item.qualified_name, item.kind, absolute, item.line, item.end_line,
             item.column, item.parent, "\n".join(item.decorators), "\n".join(item.bases), item.signature)
            for item in items
        }
        return tuple(sorted(rows, key=SymbolDatabase._row_sort_key))

    @staticmethod
    def _row_sort_key(row: tuple[object, ...]) -> tuple[str, int, int, str]:
        return (str(row[3]).casefold(), int(row[4]), int(row[6]), str(row[1]).casefold())

    @staticmethod
    def _insert_rows(connection: sqlite3.Connection, rows: Iterable[tuple[object, ...]]) -> None:
        connection.executemany(
            """INSERT INTO symbols
            (name, qualified_name, kind, path, line, end_line, column_no, parent, decorators, bases, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    @staticmethod
    def _select_rows_for_file(connection: sqlite3.Connection, absolute: str) -> tuple[tuple[object, ...], ...]:
        rows = connection.execute(
            "SELECT name, qualified_name, kind, path, line, end_line, column_no, parent, decorators, bases, signature FROM symbols WHERE path = ?",
            (absolute,),
        ).fetchall()
        return tuple(sorted((tuple(row) for row in rows), key=SymbolDatabase._row_sort_key))

    @staticmethod
    def _select_all_rows(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
        rows = connection.execute(
            "SELECT name, qualified_name, kind, path, line, end_line, column_no, parent, decorators, bases, signature FROM symbols"
        ).fetchall()
        return tuple(sorted((tuple(row) for row in rows), key=SymbolDatabase._row_sort_key))

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
    def _row_to_record(row: tuple[object, ...]) -> SymbolRecord:
        return SymbolRecord(
            name=str(row[0]), qualified_name=str(row[1]), kind=str(row[2]), path=str(row[3]),
            line=int(row[4]), end_line=int(row[5]), column=int(row[6]),
            parent=str(row[7]) if row[7] is not None else None,
            decorators=tuple(filter(None, str(row[8]).splitlines())),
            bases=tuple(filter(None, str(row[9]).splitlines())), signature=str(row[10]),
        )
