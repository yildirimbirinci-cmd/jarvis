from pathlib import Path

from artmach_assistant.indexing.project_symbol_index import ProjectSymbolIndex
from artmach_assistant.indexing.project_symbol_registry import ProjectSymbolRegistry
from artmach_assistant.indexing.symbol_parser import SymbolRecord


def _record(path: Path, name: str = "Thing") -> SymbolRecord:
    return SymbolRecord(
        name=name, qualified_name=name, kind="class", path=str(path),
        line=1, end_line=2, column=0, parent=None, bases=(), signature="",
    )


def test_registry_deduplicates_identical_symbols(tmp_path: Path) -> None:
    source = tmp_path / "mod.py"
    source.write_text("class Thing:\n    pass\n", encoding="utf-8")
    record = _record(source)
    registry = ProjectSymbolRegistry(tmp_path)
    registry.replace_file(source, [record, record])
    assert len(registry.symbols_for_file(source)) == 1
    assert registry.stats() == {"project_symbols": 1, "project_symbol_files": 1}


def test_registry_query_methods_reject_non_text_values(tmp_path: Path) -> None:
    registry = ProjectSymbolRegistry(tmp_path)
    assert registry.exact(None) == ()  # type: ignore[arg-type]
    assert registry.search(123) == ()  # type: ignore[arg-type]
    assert registry.canonical(object()) == ()  # type: ignore[arg-type]
    assert registry.module_symbols([]) == ()  # type: ignore[arg-type]


def test_project_index_rejects_non_text_query(tmp_path: Path) -> None:
    class StubIndex:
        def symbols_for_file(self, path):
            return ()

    index = ProjectSymbolIndex(tmp_path, StubIndex())  # type: ignore[arg-type]
    assert index.resolve(None) == ()  # type: ignore[arg-type]
