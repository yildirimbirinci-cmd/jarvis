from pathlib import Path

import pytest

from artmach_assistant.indexing.project_symbol_index import ProjectSymbolIndex
from artmach_assistant.indexing.symbol_parser import SymbolRecord


class _SymbolIndex:
    def __init__(self) -> None:
        self.fail = False

    def symbols_for_file(self, path: Path):
        if self.fail:
            raise RuntimeError("parse failed")
        return (SymbolRecord(path.stem, path.stem, "function", str(path), 1, 1, 0),)


def test_rebuild_failure_preserves_previous_registry(tmp_path: Path) -> None:
    source = tmp_path / "old.py"
    source.write_text("def old(): pass\n", encoding="utf-8")
    backend = _SymbolIndex()
    index = ProjectSymbolIndex(tmp_path, backend)
    assert index.rebuild((source,)) is True
    before = index.snapshot()

    backend.fail = True
    with pytest.raises(RuntimeError, match="parse failed"):
        index.rebuild((source,))

    assert index.snapshot() == before
