from __future__ import annotations

from pathlib import Path

import pytest

from pkg42work.indexing.project_symbol_index import ProjectSymbolIndex
from pkg42work.indexing.symbol_reference_index import SymbolReferenceIndex


class ExplodingIterable:
    def __init__(self, error_type):
        self.error_type = error_type

    def __iter__(self):
        raise self.error_type("boom")


class DummySymbolIndex:
    def symbols_for_file(self, path: Path):
        return ()


@pytest.mark.parametrize("error_type", [MemoryError, RecursionError, OverflowError])
def test_project_symbol_rebuild_rejects_catastrophic_iterable_atomically(tmp_path: Path, error_type):
    source = tmp_path / "a.py"
    source.write_text("x = 1\n", encoding="utf-8")
    index = ProjectSymbolIndex(tmp_path, DummySymbolIndex())
    index.rebuild([source])
    before = index.stats()

    with pytest.raises(ValueError):
        index.rebuild(ExplodingIterable(error_type))

    assert index.stats() == before


@pytest.mark.parametrize("error_type", [MemoryError, RecursionError, OverflowError])
def test_reference_rebuild_rejects_catastrophic_iterable_atomically(tmp_path: Path, error_type):
    source = tmp_path / "a.py"
    source.write_text("x = 1\n", encoding="utf-8")
    index = SymbolReferenceIndex(tmp_path)
    index.rebuild([source])
    before = index.stats()

    with pytest.raises(ValueError):
        index.rebuild(ExplodingIterable(error_type))

    assert index.stats() == before
