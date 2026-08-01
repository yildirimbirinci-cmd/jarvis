from __future__ import annotations

from pathlib import Path

import pytest

from pkg45work.indexing.symbol_database import SymbolDatabase
from pkg45work.indexing.symbol_parser import SymbolRecord
from pkg45work.indexing.symbol_reference_database import SymbolReferenceDatabase
from pkg45work.indexing.symbol_reference_parser import SymbolReferenceRecord


class ExplodingIterable:
    def __iter__(self):
        yield object()
        raise RuntimeError("boom")


def _symbol(source: Path, name: str = "seed") -> SymbolRecord:
    return SymbolRecord(name, name, "function", str(source), 1, 1, 0)


def _reference(source: Path, name: str = "seed") -> SymbolReferenceRecord:
    return SymbolReferenceRecord(name, str(source), 1, 0, "load")


def test_symbol_database_rejects_exploding_iterable_atomically(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SymbolDatabase(tmp_path, directory=tmp_path / "symbols")
    database.replace_file(source, (_symbol(source),))
    before = database.symbols_for_file(source)

    with pytest.raises(ValueError):
        database.replace_file(source, ExplodingIterable())

    assert database.symbols_for_file(source) == before


def test_symbol_database_rejects_invalid_record_types_atomically(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SymbolDatabase(tmp_path, directory=tmp_path / "symbols")
    database.replace_file(source, (_symbol(source),))

    with pytest.raises(TypeError):
        database.replace_file(source, (object(),))

    assert database.symbols_for_file(source) == (_symbol(source),)


def test_reference_database_rejects_exploding_iterable_atomically(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SymbolReferenceDatabase(tmp_path, directory=tmp_path / "references")
    database.replace_file(source, (_reference(source),))
    before = database.references_for_file(source)

    with pytest.raises(ValueError):
        database.replace_file(source, ExplodingIterable())

    assert database.references_for_file(source) == before


def test_reference_database_rejects_invalid_record_types_atomically(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SymbolReferenceDatabase(tmp_path, directory=tmp_path / "references")
    database.replace_file(source, (_reference(source),))

    with pytest.raises(TypeError):
        database.replace_file(source, (object(),))

    assert database.references_for_file(source) == (_reference(source),)
