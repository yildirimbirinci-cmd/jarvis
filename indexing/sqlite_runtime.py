"""Shared SQLite concurrency policy for ECHO indexing stores."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator

_REGISTRY_GUARD = RLock()
_PATH_LOCKS: dict[str, RLock] = {}

SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30_000


def path_lock(path: str | Path) -> RLock:
    key = str(Path(path).expanduser().resolve(strict=False))
    with _REGISTRY_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _PATH_LOCKS[key] = lock
        return lock


def open_connection(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=SQLITE_TIMEOUT_SECONDS)
    try:
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection
    except Exception:
        # Initialization PRAGMAs run before the connection reaches the
        # transaction() context manager. If one of them fails (for example on
        # a corrupt database), close here so Windows does not retain the file
        # handle and block quarantine/recovery.
        connection.close()
        raise


def is_transient_lock_error(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return "database is locked" in text or "database table is locked" in text or "database is busy" in text


@contextmanager
def transaction(path: str | Path, *, foreign_keys: bool = False) -> Iterator[sqlite3.Connection]:
    lock = path_lock(path)
    with lock:
        connection = open_connection(path)
        try:
            if foreign_keys:
                connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
