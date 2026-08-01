from pathlib import Path

from artmach_assistant.indexing.project_symbol_registry import ProjectSymbolRegistry
from artmach_assistant.indexing.symbol_parser import SymbolRecord


def _record(path: Path, name: str = "target") -> SymbolRecord:
    return SymbolRecord(name, name, "function", str(path), 1, 1, 0)


def test_registry_revision_changes_only_on_real_mutation(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("def target(): pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(tmp_path)

    assert registry.revision == 0
    assert registry.replace_file(source, (_record(source),)) is True
    assert registry.revision == 1
    assert registry.replace_file(source, (_record(source),)) is False
    assert registry.revision == 1
    assert registry.remove_file(tmp_path / "missing.py") is False
    assert registry.revision == 1
