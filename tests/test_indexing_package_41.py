from __future__ import annotations

from pathlib import Path

import pytest

from pkg41work.indexing.dependency_graph import DependencyGraph


class _FailingIterable:
    def __iter__(self):
        yield "first.py"
        raise MemoryError("simulated failure")


def test_replace_dependencies_is_atomic_for_failing_iterable(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    original = tmp_path / "original.py"
    graph = DependencyGraph()
    graph.replace_dependencies(source, (original,))
    before = graph.to_dict()

    with pytest.raises(ValueError, match="dependencies iterable failed"):
        graph.replace_dependencies(source, _FailingIterable())

    assert graph.to_dict() == before


def test_replace_dependencies_accepts_single_path_value(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    dependency = tmp_path / "dependency.py"
    graph = DependencyGraph()

    graph.replace_dependencies(source, dependency)

    assert graph.dependencies_of(source) == (str(dependency.resolve()),)


def test_replace_dependencies_rejects_invalid_item_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    dependency = tmp_path / "dependency.py"
    graph = DependencyGraph()
    graph.replace_dependencies(source, (dependency,))
    before = graph.to_dict()

    with pytest.raises(ValueError, match="dependencies iterable failed"):
        graph.replace_dependencies(source, (dependency, object()))

    assert graph.to_dict() == before
