from pathlib import Path

import pytest

from pkg46verified.indexing.cross_file_reference_resolver import ReferenceBindingResult, ResolvedSymbolReference
from pkg46verified.indexing.global_symbol_graph import GlobalSymbolGraph
from pkg46verified.indexing.project_symbol_registry import ProjectSymbolRegistry
from pkg46verified.indexing.symbol_parser import SymbolRecord
from pkg46verified.indexing.symbol_reference_parser import SymbolReferenceRecord


def _record(path: Path, name: str = "target") -> SymbolRecord:
    return SymbolRecord(name, name, "function", str(path), 1, 1, 0)


def _reference(path: Path, name: str = "target") -> SymbolReferenceRecord:
    return SymbolReferenceRecord(name, str(path), 3, 2, "call", None)


def _bound_result(source: Path, registry: ProjectSymbolRegistry, target: Path, *, duplicate: bool = False):
    symbol = registry.symbols_for_file(target)[0]
    definitions = (symbol, symbol) if duplicate else (symbol,)
    resolved = ResolvedSymbolReference(_reference(source), definitions)
    return ReferenceBindingResult(str(source), (resolved,))


def test_registry_failing_iterable_preserves_previous_symbols(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("def old(): pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(tmp_path)
    registry.replace_file(source, (_record(source, "old"),))

    def broken():
        yield _record(source, "new")
        raise RuntimeError("boom")

    with pytest.raises(ValueError, match="symbols iterable failed"):
        registry.replace_file(source, broken())
    assert [item.name for item in registry.symbols_for_file(source)] == ["old"]


def test_registry_invalid_item_preserves_previous_symbols(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("def old(): pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(tmp_path)
    registry.replace_file(source, (_record(source, "old"),))
    with pytest.raises(TypeError, match="SymbolRecord"):
        registry.replace_file(source, (object(),))
    assert [item.name for item in registry.symbols_for_file(source)] == ["old"]


def test_graph_failing_results_iterable_is_atomic(tmp_path: Path) -> None:
    source = tmp_path / "source.py"; source.write_text("target()\n", encoding="utf-8")
    target = tmp_path / "target.py"; target.write_text("def target(): pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(tmp_path); registry.replace_file(target, (_record(target),))
    graph = GlobalSymbolGraph(tmp_path, registry)
    result = _bound_result(source, registry, target)
    assert graph.replace_file(result) is True
    before = graph.snapshot()

    def broken():
        yield result
        raise RuntimeError("boom")

    with pytest.raises(ValueError, match="results iterable failed"):
        graph.replace_files(broken())
    assert graph.snapshot() == before


def test_graph_rejects_source_outside_root_without_mutation(tmp_path: Path) -> None:
    target = tmp_path / "target.py"; target.write_text("def target(): pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(tmp_path); registry.replace_file(target, (_record(target),))
    graph = GlobalSymbolGraph(tmp_path, registry)
    outside = tmp_path.parent / "outside.py"
    result = _bound_result(outside, registry, target)
    with pytest.raises(ValueError, match="outside project root"):
        graph.replace_file(result)
    assert graph.stats()["global_symbol_revision"] == 0
    assert graph.stats()["global_symbol_edges"] == 0


def test_graph_deduplicates_edges_and_only_revises_on_change(tmp_path: Path) -> None:
    source = tmp_path / "source.py"; source.write_text("target()\n", encoding="utf-8")
    target = tmp_path / "target.py"; target.write_text("def target(): pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(tmp_path); registry.replace_file(target, (_record(target),))
    graph = GlobalSymbolGraph(tmp_path, registry)
    result = _bound_result(source, registry, target, duplicate=True)
    assert graph.replace_file(result) is True
    assert graph.stats()["global_symbol_edges"] == 1
    assert graph.stats()["global_symbol_revision"] == 1
    assert graph.replace_file(result) is False
    assert graph.stats()["global_symbol_revision"] == 1
