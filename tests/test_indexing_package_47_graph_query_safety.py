from pathlib import Path

from indexing.global_symbol_graph import GlobalSymbolGraph
from indexing.project_symbol_registry import ProjectSymbolRegistry


def test_graph_queries_reject_invalid_names_and_limits_safely(tmp_path: Path) -> None:
    graph = GlobalSymbolGraph(tmp_path, ProjectSymbolRegistry(tmp_path))
    assert graph.incoming(None) == ()
    assert graph.incoming("   ") == ()
    assert graph.related_symbols("missing", limit="invalid") == ()
    assert graph.clear() is False
