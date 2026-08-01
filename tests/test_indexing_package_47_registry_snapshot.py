import json
from pathlib import Path

from artmach_assistant.indexing.project_symbol_registry import ProjectSymbolRegistry
from artmach_assistant.indexing.symbol_parser import SymbolRecord


def _record(path: Path, name: str) -> SymbolRecord:
    return SymbolRecord(name, name, "function", str(path), 1, 1, 0)


def test_registry_snapshot_is_deterministic_and_json_serializable(tmp_path: Path) -> None:
    first = tmp_path / "b.py"
    second = tmp_path / "a.py"
    first.write_text("def b(): pass\n", encoding="utf-8")
    second.write_text("def a(): pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(tmp_path)
    registry.replace_file(first, (_record(first, "b"),))
    registry.replace_file(second, (_record(second, "a"),))

    snapshot = registry.snapshot()
    assert list(snapshot["files"]) == sorted(snapshot["files"], key=str.casefold)
    assert json.loads(json.dumps(snapshot)) == snapshot
    assert snapshot["revision"] == 2
