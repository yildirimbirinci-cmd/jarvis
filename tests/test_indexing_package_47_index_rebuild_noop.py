from pathlib import Path

from artmach_assistant.indexing.project_symbol_index import ProjectSymbolIndex
from artmach_assistant.indexing.symbol_parser import SymbolRecord


class _SymbolIndex:
    def symbols_for_file(self, path: Path):
        return (SymbolRecord(path.stem, path.stem, "function", str(path), 1, 1, 0),)


def test_rebuild_reports_noop_for_identical_content(tmp_path: Path) -> None:
    source = tmp_path / "same.py"
    source.write_text("def same(): pass\n", encoding="utf-8")
    index = ProjectSymbolIndex(tmp_path, _SymbolIndex())

    assert index.rebuild((source,)) is True
    assert index.rebuild((source,)) is False
    assert index.resolve("same")[0].name == "same"
