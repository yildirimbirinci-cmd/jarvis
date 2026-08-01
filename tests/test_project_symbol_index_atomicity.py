from pathlib import Path
import pytest

from artmach_assistant.indexing.project_symbol_index import ProjectSymbolIndex
from artmach_assistant.indexing.project_symbol_registry import ProjectSymbolRegistry
from artmach_assistant.indexing.symbol_parser import SymbolRecord


class FakeSymbolIndex:
    def __init__(self, records, failing=None):
        self.records = records
        self.failing = {str(Path(p).resolve()) for p in (failing or ())}

    def symbols_for_file(self, path):
        key = str(Path(path).resolve())
        if key in self.failing:
            raise RuntimeError("transient indexing failure")
        return self.records.get(key, ())


def record(path, name):
    return SymbolRecord(name=name, qualified_name=name, kind="function", path=str(path))


def test_rebuild_is_atomic_when_one_file_fails(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("pass\n", encoding="utf-8")
    second.write_text("pass\n", encoding="utf-8")

    initial = FakeSymbolIndex({str(first.resolve()): (record(first, "stable"),)})
    index = ProjectSymbolIndex(tmp_path, initial)
    index.rebuild((first,))

    failing = FakeSymbolIndex(
        {
            str(first.resolve()): (record(first, "changed"),),
            str(second.resolve()): (record(second, "new"),),
        },
        failing=(second,),
    )
    index._symbol_index = failing

    with pytest.raises(RuntimeError):
        index.rebuild((first, second))

    assert [item.name for item in index.resolve("stable")] == ["stable"]
    assert index.resolve("changed") == ()


def test_successful_rebuild_replaces_registry_as_one_snapshot(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("pass\n", encoding="utf-8")
    second.write_text("pass\n", encoding="utf-8")
    fake = FakeSymbolIndex(
        {
            str(first.resolve()): (record(first, "one"),),
            str(second.resolve()): (record(second, "two"),),
        }
    )
    index = ProjectSymbolIndex(tmp_path, fake)
    index.rebuild((first, second))
    assert index.stats() == {"project_symbols": 2, "project_symbol_files": 2}


def test_registry_rejects_paths_outside_project_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(root)

    with pytest.raises(ValueError):
        registry.replace_file(outside, (record(outside, "external"),))
    with pytest.raises(ValueError):
        registry.symbols_for_file(outside)
