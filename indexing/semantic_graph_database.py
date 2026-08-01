"""Persistent SQLite storage for the incremental semantic code graph."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterable, Iterator

from artmach_assistant.config import DATA_DIR

from .semantic_graph_builder import SemanticEdge, SemanticNode


class SemanticGraphDatabase:
    SCHEMA_VERSION = 3
    MAX_METADATA_BYTES = 1_048_576
    REQUIRED_TABLES = frozenset({"semantic_nodes", "semantic_edges", "semantic_meta"})

    def __init__(self, project_root: str | Path, directory: str | Path | None = None) -> None:
        self.project_root = Path(project_root).expanduser().resolve(strict=False)
        if not self.project_root.is_dir():
            raise NotADirectoryError(str(self.project_root))
        digest = hashlib.sha256(
            os.path.normcase(str(self.project_root)).encode("utf-8", errors="replace")
        ).hexdigest()[:24]
        if isinstance(directory, str) and not directory.strip():
            raise ValueError("semantic graph directory cannot be empty")
        base = (
            Path(directory).expanduser().resolve(strict=False)
            if directory is not None
            else DATA_DIR / "semantic_graphs"
        )
        self.path = base / f"{digest}.sqlite3"
        self._lock = RLock()
        self._initialize_with_recovery()

    @property
    def revision(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM semantic_meta WHERE key='revision'"
            ).fetchone()
            return max(0, int(row[0])) if row else 0

    def replace_file(self, path, nodes, edges) -> bool:
        absolute = str(self._resolve_path(path))
        node_rows, edge_rows = self._prepare(absolute, nodes, edges)
        with self._lock, self._connect() as connection:
            old_nodes = connection.execute(
                "SELECT node_id,kind,name,qualified_name,path,line,end_line,metadata "
                "FROM semantic_nodes WHERE path=? ORDER BY node_id",
                (absolute,),
            ).fetchall()
            old_edges = connection.execute(
                "SELECT source_id,target,kind,path,line,metadata "
                "FROM semantic_edges WHERE path=? ORDER BY source_id,target,kind,line",
                (absolute,),
            ).fetchall()
            if old_nodes == node_rows and old_edges == edge_rows:
                return False
            connection.execute("DELETE FROM semantic_edges WHERE path=?", (absolute,))
            connection.execute("DELETE FROM semantic_nodes WHERE path=?", (absolute,))
            connection.executemany(
                "INSERT INTO semantic_nodes VALUES (?,?,?,?,?,?,?,?)", node_rows
            )
            connection.executemany(
                "INSERT INTO semantic_edges "
                "(source_id,target,kind,path,line,metadata) VALUES (?,?,?,?,?,?)",
                edge_rows,
            )
            self._bump(connection)
            return True

    def replace_all(self, replacements) -> bool:
        if replacements is None or isinstance(
            replacements, (str, bytes, bytearray, memoryview, Path)
        ):
            raise TypeError("replacements must be iterable")
        try:
            items = tuple(replacements)
        except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError) as exc:
            raise ValueError("replacements iterable failed") from exc
        node_rows: list[tuple] = []
        edge_rows: list[tuple] = []
        for item in items:
            if not isinstance(item, tuple) or len(item) != 3:
                raise TypeError("each replacement must be a (path, nodes, edges) tuple")
            nodes, edges = self._prepare(str(self._resolve_path(item[0])), item[1], item[2])
            node_rows.extend(nodes)
            edge_rows.extend(edges)
        node_rows = sorted(set(node_rows))
        edge_rows = sorted(set(edge_rows))
        node_ids = [row[0] for row in node_rows]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("semantic node ids must be unique across replacements")
        with self._lock, self._connect() as connection:
            old_nodes = connection.execute(
                "SELECT node_id,kind,name,qualified_name,path,line,end_line,metadata "
                "FROM semantic_nodes ORDER BY node_id"
            ).fetchall()
            old_edges = connection.execute(
                "SELECT source_id,target,kind,path,line,metadata "
                "FROM semantic_edges ORDER BY source_id,target,kind,line"
            ).fetchall()
            if old_nodes == node_rows and old_edges == edge_rows:
                return False
            connection.execute("DELETE FROM semantic_edges")
            connection.execute("DELETE FROM semantic_nodes")
            connection.executemany(
                "INSERT INTO semantic_nodes VALUES (?,?,?,?,?,?,?,?)", node_rows
            )
            connection.executemany(
                "INSERT INTO semantic_edges "
                "(source_id,target,kind,path,line,metadata) VALUES (?,?,?,?,?,?)",
                edge_rows,
            )
            self._bump(connection)
            return True

    def remove_file(self, path) -> bool:
        absolute = str(self._resolve_path(path))
        with self._lock, self._connect() as connection:
            changed = bool(
                connection.execute(
                    "SELECT 1 FROM semantic_nodes WHERE path=? "
                    "UNION SELECT 1 FROM semantic_edges WHERE path=? LIMIT 1",
                    (absolute, absolute),
                ).fetchone()
            )
            if not changed:
                return False
            connection.execute("DELETE FROM semantic_edges WHERE path=?", (absolute,))
            connection.execute("DELETE FROM semantic_nodes WHERE path=?", (absolute,))
            self._bump(connection)
            return True

    def clear(self) -> bool:
        with self._lock, self._connect() as connection:
            if not connection.execute(
                "SELECT 1 FROM semantic_nodes UNION SELECT 1 FROM semantic_edges LIMIT 1"
            ).fetchone():
                return False
            connection.execute("DELETE FROM semantic_edges")
            connection.execute("DELETE FROM semantic_nodes")
            self._bump(connection)
            return True

    def snapshot(self) -> dict[str, object]:
        with self._lock, self._connect() as connection:
            files = [
                row[0]
                for row in connection.execute(
                    "SELECT path FROM semantic_nodes UNION SELECT path FROM semantic_edges ORDER BY 1"
                ).fetchall()
            ]
            nodes = int(connection.execute("SELECT COUNT(*) FROM semantic_nodes").fetchone()[0])
            edges = int(connection.execute("SELECT COUNT(*) FROM semantic_edges").fetchone()[0])
            revision_row = connection.execute(
                "SELECT value FROM semantic_meta WHERE key='revision'"
            ).fetchone()
            revision = max(0, int(revision_row[0])) if revision_row else 0
            return {
                "revision": revision,
                "files": files,
                "stats": {
                    "semantic_nodes": nodes,
                    "semantic_edges": edges,
                    "semantic_files": len(files),
                    "semantic_revision": revision,
                },
            }

    def edges_for_target(self, target: str, *, kinds: Iterable[str] = (), limit: int = 500):
        if not isinstance(target, str) or not target.strip():
            return ()
        if kinds is None:
            raise TypeError("kinds must be iterable")
        else:
            if not isinstance(kinds, str):
                try:
                    iter(kinds)
                except TypeError as exc:
                    raise TypeError("kinds must be iterable") from exc
                except (ValueError, RuntimeError, OverflowError, MemoryError, RecursionError):
                    return ()
            values = (kinds,) if isinstance(kinds, str) else kinds
            try:
                staged = tuple(values)
            except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError):
                return ()
            kind_values = tuple(
                dict.fromkeys(value for item in staged if (value := str(item).strip()))
            )
        sql = (
            "SELECT source_id,target,kind,path,line,metadata "
            "FROM semantic_edges WHERE target=?"
        )
        params: list[object] = [target.strip()]
        if kind_values:
            sql += f" AND kind IN ({','.join('?' for _ in kind_values)})"
            params.extend(kind_values)
        sql += " ORDER BY path COLLATE NOCASE,line,source_id LIMIT ?"
        params.append(self._safe_limit(limit))
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return tuple(
            SemanticEdge(row[0], row[1], row[2], row[3], row[4], self._decode_metadata(row[5]))
            for row in rows
        )

    def integrity_check(self) -> bool:
        try:
            with self._lock, self._connect() as connection:
                row = connection.execute("PRAGMA quick_check").fetchone()
                return bool(row and str(row[0]).casefold() == "ok" and self._schema_valid(connection))
        except sqlite3.DatabaseError:
            return False

    def stats(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            nodes = int(connection.execute("SELECT COUNT(*) FROM semantic_nodes").fetchone()[0])
            edges = int(connection.execute("SELECT COUNT(*) FROM semantic_edges").fetchone()[0])
            files = int(
                connection.execute(
                    "SELECT COUNT(*) FROM (SELECT path FROM semantic_nodes UNION SELECT path FROM semantic_edges)"
                ).fetchone()[0]
            )
            revision_row = connection.execute(
                "SELECT value FROM semantic_meta WHERE key='revision'"
            ).fetchone()
            revision = max(0, int(revision_row[0])) if revision_row else 0
        return {
            "semantic_nodes": nodes,
            "semantic_edges": edges,
            "semantic_files": files,
            "semantic_revision": revision,
        }

    def _prepare(self, absolute, nodes, edges):
        node_items = self._materialize_records(nodes, SemanticNode, "nodes")
        edge_items = self._materialize_records(edges, SemanticEdge, "edges")
        node_rows = sorted(
            {
                (
                    item.node_id,
                    item.kind,
                    item.name,
                    item.qualified_name,
                    absolute,
                    max(0, int(item.line)),
                    max(0, int(item.end_line)),
                    self._encode_metadata(item.metadata),
                )
                for item in node_items
            }
        )
        edge_rows = sorted(
            {
                (
                    item.source_id,
                    item.target,
                    item.kind,
                    absolute,
                    max(0, int(item.line)),
                    self._encode_metadata(item.metadata),
                )
                for item in edge_items
            }
        )
        return node_rows, edge_rows

    def _resolve_path(self, path):
        candidate = Path(path).expanduser()
        candidate = (
            self.project_root / candidate if not candidate.is_absolute() else candidate
        ).resolve(strict=False)
        try:
            candidate.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(f"path is outside project root: {candidate}") from exc
        return candidate

    @staticmethod
    def _safe_limit(value, default=500):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return default if not math.isfinite(number) else min(10_000, max(1, int(number)))

    @staticmethod
    def _materialize_records(values, expected_type, label):
        if values is None or isinstance(values, (str, bytes, bytearray, memoryview, Path)):
            raise TypeError(f"{label} must be iterable")
        try:
            items = tuple(values)
        except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError) as exc:
            raise ValueError(f"{label} iterable failed") from exc
        if any(not isinstance(item, expected_type) for item in items):
            raise TypeError(f"{label} must contain only {expected_type.__name__} records")
        return items

    @classmethod
    def _encode_metadata(cls, value):
        try:
            encoded = json.dumps(
                dict(value or {}),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("metadata must contain valid JSON values") from exc
        if len(encoded.encode("utf-8")) > cls.MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds maximum size")
        return encoded

    @classmethod
    def _decode_metadata(cls, value):
        try:
            raw = str(value or "{}")
            if len(raw.encode("utf-8")) > cls.MAX_METADATA_BYTES:
                return ()

            def reject_duplicates(pairs):
                payload = {}
                for key, item in pairs:
                    if key in payload:
                        raise ValueError("duplicate metadata key")
                    payload[key] = item
                return payload

            payload = json.loads(
                raw,
                object_pairs_hook=reject_duplicates,
                parse_constant=lambda constant: (_ for _ in ()).throw(
                    ValueError(f"non-finite metadata value: {constant}")
                ),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
        return (
            tuple(sorted((str(key), str(item)) for key, item in payload.items()))
            if isinstance(payload, dict)
            else ()
        )

    def _initialize_with_recovery(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.DatabaseError:
            self._quarantine_corrupt_database()
            self._initialize()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS semantic_nodes (
                    node_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS semantic_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS semantic_meta (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO semantic_meta(key,value) VALUES ('revision',0);
                CREATE INDEX IF NOT EXISTS idx_semantic_nodes_path ON semantic_nodes(path);
                CREATE INDEX IF NOT EXISTS idx_semantic_edges_path ON semantic_edges(path);
                CREATE INDEX IF NOT EXISTS idx_semantic_edges_target ON semantic_edges(target);
                """
            )
            connection.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
            if not self._schema_valid(connection):
                raise sqlite3.DatabaseError("semantic graph schema is invalid")

    @classmethod
    def _schema_valid(cls, connection: sqlite3.Connection) -> bool:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        return cls.REQUIRED_TABLES.issubset(tables)

    def _quarantine_corrupt_database(self) -> None:
        for candidate in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            try:
                if candidate.exists():
                    if candidate == self.path:
                        quarantine = self.path.with_suffix(self.path.suffix + ".corrupt")
                        counter = 1
                        while quarantine.exists():
                            quarantine = self.path.with_suffix(
                                self.path.suffix + f".corrupt.{counter}"
                            )
                            counter += 1
                        candidate.replace(quarantine)
                    else:
                        candidate.unlink()
            except OSError:
                if candidate == self.path:
                    raise

    @staticmethod
    def _bump(connection):
        connection.execute(
            "UPDATE semantic_meta SET value=value+1 WHERE key='revision'"
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15.0)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
