from pathlib import Path

import pytest

from artmach_assistant.indexing.dependency_graph import DependencyGraph
from artmach_assistant.indexing.dependency_resolver import DependencyResolver
from artmach_assistant.indexing.symbol_graph_update_planner import SymbolGraphUpdatePlanner


def test_dependency_graph_accepts_single_path_dependency(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    dependency = tmp_path / "dependency.py"
    graph = DependencyGraph()

    graph.replace_dependencies(source, dependency)

    assert graph.dependencies_of(source) == (str(dependency.resolve()),)


def test_dependency_graph_load_is_atomic_on_invalid_path(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    dependency = tmp_path / "dependency.py"
    graph = DependencyGraph()
    graph.replace_dependencies(source, (dependency,))
    before = graph.to_dict()

    with pytest.raises((TypeError, ValueError, OSError)):
        graph.load_dict({"valid.py": ["\x00invalid"]})

    assert graph.to_dict() == before


def test_dependency_resolver_accepts_single_suffix_string(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("value = 1\n", encoding="utf-8")

    resolver = DependencyResolver(tmp_path, source_suffixes="py")

    assert tuple(path.name for path in resolver.source_paths()) == ()
    results = resolver.rebuild()
    assert tuple(Path(item.path).name for item in results) == ("module.py",)


def test_update_planner_accepts_single_string_path(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    resolver = DependencyResolver(tmp_path)
    resolver.rebuild()
    planner = SymbolGraphUpdatePlanner(tmp_path, resolver)

    planner.capture_before("source.py")
    plan = planner.finalize()

    assert plan.changed == (source.resolve(),)
    assert plan.rebind == (source.resolve(),)
