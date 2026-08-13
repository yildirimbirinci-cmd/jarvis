from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from artmach_assistant.indexing.semantic_graph_database import SemanticGraphDatabase
from artmach_assistant.indexing.sqlite_runtime import path_lock
from artmach_assistant.indexing.symbol_database import SymbolDatabase
from artmach_assistant.indexing.symbol_reference_database import SymbolReferenceDatabase


def test_indexing_databases_share_one_process_lock_per_database_path(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    first = SymbolDatabase(root, tmp_path / "symbols")
    second = SymbolDatabase(root, tmp_path / "symbols")
    assert first.path == second.path
    assert first._lock is second._lock

    first_ref = SymbolReferenceDatabase(root, tmp_path / "refs")
    second_ref = SymbolReferenceDatabase(root, tmp_path / "refs")
    assert first_ref._lock is second_ref._lock

    first_sem = SemanticGraphDatabase(root, tmp_path / "semantic")
    second_sem = SemanticGraphDatabase(root, tmp_path / "semantic")
    assert first_sem._lock is second_sem._lock


def test_semantic_transient_lock_is_not_quarantined_as_corruption(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    directory = tmp_path / "semantic"
    directory.mkdir()

    db = object.__new__(SemanticGraphDatabase)
    db.project_root = root
    db.path = directory / "locked.sqlite3"
    db._lock = path_lock(db.path)
    db.path.write_bytes(b"live database placeholder")

    calls = {"initialize": 0, "quarantine": 0}

    def locked_initialize() -> None:
        calls["initialize"] += 1
        raise sqlite3.OperationalError("database is locked")

    def quarantine() -> None:
        calls["quarantine"] += 1

    monkeypatch.setattr(db, "_initialize", locked_initialize)
    monkeypatch.setattr(db, "_quarantine_corrupt_database", quarantine)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        db._initialize_with_recovery()

    assert calls == {"initialize": 1, "quarantine": 0}
    assert db.path.exists()


def test_same_path_writers_are_serialized_in_process(tmp_path: Path) -> None:
    path = tmp_path / "shared.sqlite3"
    lock_a = path_lock(path)
    lock_b = path_lock(path)
    assert lock_a is lock_b

    entered: list[str] = []
    release = threading.Event()
    second_entered = threading.Event()

    def first() -> None:
        with lock_a:
            entered.append("first")
            release.wait(2)

    def second() -> None:
        with lock_b:
            entered.append("second")
            second_entered.set()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    while entered != ["first"]:
        pass
    t2.start()
    assert not second_entered.wait(0.05)
    release.set()
    t1.join(2)
    t2.join(2)
    assert entered == ["first", "second"]
