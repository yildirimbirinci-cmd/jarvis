from pathlib import Path

import pytest

from artmach_assistant.indexing.cross_file_reference_resolver import (
    CrossFileReferenceResolver,
    ReferenceBindingResult,
    ResolvedSymbolReference,
)
from artmach_assistant.indexing.cross_file_symbol_resolver import CrossFileSymbolResolver
from artmach_assistant.indexing.global_symbol_graph import GlobalSymbolGraph
from artmach_assistant.indexing.project_symbol_registry import ProjectSymbol, ProjectSymbolRegistry
from artmach_assistant.indexing.symbol_reference_parser import SymbolReferenceRecord


class _NoopResolver:
    def resolve(self, *args, **kwargs):  # pragma: no cover - outside-root fails first
        raise AssertionError("resolver should not be called")


def _symbol(path: Path, name: str = "target") -> ProjectSymbol:
    return ProjectSymbol(
        name=name,
        qualified_name=name,
        canonical_name=f"mod.{name}",
        module="mod",
        kind="function",
        path=str(path),
        line=1,
        end_line=1,
        column=0,
    )


def _reference(path: Path, name: str = "target") -> SymbolReferenceRecord:
    return SymbolReferenceRecord(name, str(path), 1, 0, "call")


def test_cross_file_resolvers_reject_sources_outside_project(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(root)

    reference_resolver = CrossFileReferenceResolver(root, _NoopResolver())
    with pytest.raises(ValueError):
        reference_resolver.bind_file(outside, ())

    symbol_resolver = CrossFileSymbolResolver(root, registry)
    with pytest.raises(ValueError):
        symbol_resolver.resolve("value", source_path=outside)


def test_invalid_limits_fall_back_without_crashing(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "source.py"
    source.write_text("target()\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(root)

    symbol_resolver = CrossFileSymbolResolver(root, registry)
    result = symbol_resolver.resolve("target", source_path=source, limit="invalid")
    assert result.matches == ()

    graph = GlobalSymbolGraph(root, registry)
    assert graph.incoming("mod.target", limit="invalid") == ()
    assert graph.outgoing_for_file(source, limit=float("inf")) == ()


def test_global_graph_deduplicates_edges_and_removes_single_string_path(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "source.py"
    target = root / "target.py"
    source.write_text("target()\n", encoding="utf-8")
    target.write_text("def target():\n    pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(root)
    graph = GlobalSymbolGraph(root, registry)

    symbol = _symbol(target)
    reference = _reference(source)
    resolved = ResolvedSymbolReference(reference, (symbol, symbol), "mod.target", False)
    binding = ReferenceBindingResult(str(source), (resolved,))

    assert graph.replace_file(binding) is True
    assert len(graph.outgoing_for_file(source)) == 1
    assert graph.remove_files(str(source)) is True
    assert graph.outgoing_for_file(source) == ()


def test_global_graph_rejects_external_source_and_skips_external_target(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "source.py"
    source.write_text("target()\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("def target():\n    pass\n", encoding="utf-8")
    registry = ProjectSymbolRegistry(root)
    graph = GlobalSymbolGraph(root, registry)

    external_source = ReferenceBindingResult(str(outside), ())
    with pytest.raises(ValueError):
        graph.replace_file(external_source)

    resolved = ResolvedSymbolReference(_reference(source), (_symbol(outside),), "mod.target", False)
    assert graph.replace_file(ReferenceBindingResult(str(source), (resolved,))) is True
    assert graph.outgoing_for_file(source) == ()
