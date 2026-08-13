from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import artmach_assistant.indexing.sqlite_runtime as runtime


class _FailingConnection:
    def __init__(self) -> None:
        self.closed = False
        self.calls = 0

    def execute(self, _sql: str):
        self.calls += 1
        if self.calls == 2:
            raise sqlite3.DatabaseError("file is not a database")
        return self

    def close(self) -> None:
        self.closed = True


def test_open_connection_closes_if_initialization_pragma_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake = _FailingConnection()
    monkeypatch.setattr(runtime.sqlite3, "connect", lambda *_args, **_kwargs: fake)

    with pytest.raises(sqlite3.DatabaseError, match="file is not a database"):
        runtime.open_connection(tmp_path / "broken.sqlite3")

    assert fake.closed is True


def test_open_connection_returns_live_connection_when_pragmas_succeed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "healthy.sqlite3"
    connection = runtime.open_connection(path)
    try:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    finally:
        connection.close()
