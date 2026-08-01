from pathlib import Path

import pytest

from artmach_assistant.indexing.cross_file_reference_resolver import ReferenceBindingResult
from artmach_assistant.indexing.global_symbol_graph import GlobalSymbolGraph
from artmach_assistant.indexing.project_symbol_registry import ProjectSymbolRegistry
from artmach_assistant.indexing.symbol_parser import SymbolRecord


def _record(path: Path, name: str = "value") -> SymbolRecord:
    return SymbolRecord(
        name=name,
        qualified_name=name,
        kind="variable",
        path=str(path),
        line=1,
        end_line=1,
        column=0,
    )


def test_registry_rejects_mismatched_symbol_path_atomically(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "a.py"
    other = root / "b.py"
    source.write_text("value = 1\n", encoding="utf-8")
    other.write_text("other = 2\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(root)
    registry.replace_file(source, [_record(source)])

    with pytest.raises(ValueError):
        registry.replace_file(source, [_record(other, "other")])

    assert [item.name for item in registry.symbols_for_file(source)] == ["value"]


def test_registry_rejects_invalid_or_failing_iterables_without_mutation(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "a.py"
    source.write_text("value = 1\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(root)
    registry.replace_file(source, [_record(source)])

    with pytest.raises(TypeError):
        registry.replace_file(source, "not-symbols")

    def broken():
        yield _record(source, "replacement")
        raise RuntimeError("boom")

    with pytest.raises(ValueError):
        registry.replace_file(source, broken())

    assert [item.name for item in registry.symbols_for_file(source)] == ["value"]


def test_global_graph_rejects_bad_result_iterables_atomically(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "a.py"
    source.write_text("value = 1\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(root)
    graph = GlobalSymbolGraph(root, registry)
    initial = ReferenceBindingResult(str(source), ())
    assert graph.replace_file(initial) is True
    revision = graph.stats()["global_symbol_revision"]

    with pytest.raises(TypeError):
        graph.replace_files("not-results")

    def broken():
        yield ReferenceBindingResult(str(source), ())
        raise RuntimeError("boom")

    with pytest.raises(ValueError):
        graph.replace_files(broken())

    assert graph.stats()["global_symbol_revision"] == revision
    assert graph.snapshot()["edges_by_file"] == {str(source.resolve()): []}
