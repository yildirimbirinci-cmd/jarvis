from pathlib import Path

from artmach_assistant.indexing.project_symbol_registry import ProjectSymbolRegistry
from artmach_assistant.indexing.symbol_parser import SymbolRecord


def test_registry_clear_is_noop_when_empty(tmp_path: Path) -> None:
    registry = ProjectSymbolRegistry(tmp_path)
    assert registry.clear() is False
    assert registry.stats()["project_symbol_revision"] == 0

    source = tmp_path / "a.py"
    source.write_text("def a(): pass\n", encoding="utf-8")
    registry.replace_file(source, (SymbolRecord("a", "a", "function", str(source), 1, 1, 0),))
    assert registry.clear() is True
    assert registry.clear() is False
    assert registry.stats() == {
        "project_symbols": 0,
        "project_symbol_files": 0,
        "project_symbol_revision": 2,
    }
