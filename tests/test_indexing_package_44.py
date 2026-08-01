from __future__ import annotations

from pathlib import Path

import pytest

from pkg44work.indexing.semantic_graph import SemanticGraph
from pkg44work.indexing.semantic_graph_builder import SemanticEdge, SemanticNode
from pkg44work.indexing.semantic_graph_database import SemanticGraphDatabase


class ExplodingPaths:
    def __iter__(self):
        yield "first.py"
        raise RuntimeError("boom")


def test_rebuild_preserves_existing_graph_when_builder_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    old_source = tmp_path / "old.py"
    old_source.write_text("def old():\n    return 1\n", encoding="utf-8")
    new_source = tmp_path / "new.py"
    new_source.write_text("def new():\n    return 2\n", encoding="utf-8")

    graph = SemanticGraph(tmp_path)
    graph.rebuild((old_source,))
    before = graph.stats()

    def explode(_path: Path):
        raise RuntimeError("builder failed")

    monkeypatch.setattr(graph._builder, "parse_file", explode)
    with pytest.raises(RuntimeError, match="builder failed"):
        graph.rebuild((new_source,))

    assert graph.stats() == before
    assert graph.references_to("old") == ()


def test_rebuild_rejects_exploding_path_iterable_before_clearing(tmp_path: Path):
    source = tmp_path / "old.py"
    source.write_text("def old():\n    return 1\n", encoding="utf-8")
    graph = SemanticGraph(tmp_path)
    graph.rebuild((source,))
    before = graph.stats()

    with pytest.raises(ValueError, match="materialize paths"):
        graph.rebuild(ExplodingPaths())

    assert graph.stats() == before


def test_replace_all_is_atomic_when_a_later_record_is_invalid(tmp_path: Path):
    source = tmp_path / "old.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SemanticGraphDatabase(tmp_path, directory=tmp_path / "db")
    node = SemanticNode("old", "function", "old", "old", str(source), 1, 1, ())
    edge = SemanticEdge("old", "target", "calls", str(source), 1, ())
    database.replace_file(source, (node,), (edge,))
    before = database.stats()

    replacement = SemanticNode("new", "function", "new", "new", str(source), 2, 2, ())
    with pytest.raises(TypeError):
        database.replace_all(((source, (replacement,), ()), (source, (object(),), ())))

    assert database.stats() == before
    assert len(database.edges_for_target("target")) == 1
