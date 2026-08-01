from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from artmach_assistant.indexing.symbol_database import SymbolDatabase
from artmach_assistant.indexing.symbol_parser import SymbolRecord


def _record(path: Path) -> SymbolRecord:
    return SymbolRecord(
        name="sample",
        qualified_name="sample",
        kind="function",
        path=str(path),
        line=1,
        end_line=1,
        column=0,
        parent=None,
        decorators=(),
        bases=(),
        signature="sample()",
    )


def test_string_directory_round_trip_and_safe_limit() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp) / "project"
        root.mkdir()
        source = root / "main.py"
        source.write_text("def sample():\n    pass\n", encoding="utf-8")
        database = SymbolDatabase(root, str(Path(temp) / "cache"))

        database.replace_file(source, [_record(source)])

        assert len(database.search("sample", limit=float("inf"))) == 1
        assert len(database.search("sample", limit=float("nan"))) == 1
        assert database.symbols_for_file("main.py")[0].name == "sample"


def test_invalid_directory_and_outside_paths_are_rejected() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp) / "project"
        root.mkdir()
        with pytest.raises(ValueError):
            SymbolDatabase(root, "")
        with pytest.raises(ValueError):
            SymbolDatabase(root, "bad\x00path")
        with pytest.raises(TypeError):
            SymbolDatabase(root, 123)  # type: ignore[arg-type]

        database = SymbolDatabase(root, Path(temp) / "cache")
        with pytest.raises(ValueError):
            database.symbols_for_file(Path(temp) / "outside.py")


def test_corrupt_schema_version_is_recovered() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp) / "project"
        root.mkdir()
        directory = Path(temp) / "cache"
        database = SymbolDatabase(root, directory)
        with closing(sqlite3.connect(database.path)) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', 'corrupt')"
            )

        reopened = SymbolDatabase(root, directory)

        assert reopened.stats() == {"symbols": 0, "files": 0}
