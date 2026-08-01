from __future__ import annotations

from pathlib import Path

import pytest

from pkg43work.indexing.semantic_graph_builder import SemanticEdge, SemanticNode
from pkg43work.indexing.semantic_graph_database import SemanticGraphDatabase


class ExplodingIterable:
    def __iter__(self):
        yield object()
        raise RuntimeError("boom")


def _seed(database: SemanticGraphDatabase, source: Path) -> None:
    node = SemanticNode("node", "function", "caller", "caller", str(source), 1, 1, ())
    edge = SemanticEdge("node", "target", "call", str(source), 1, ())
    database.replace_file(source, (node,), (edge,))


def test_replace_file_rejects_exploding_nodes_atomically(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SemanticGraphDatabase(tmp_path, directory=tmp_path / "db")
    _seed(database, source)
    before = database.stats()

    with pytest.raises(ValueError):
        database.replace_file(source, ExplodingIterable(), ())

    assert database.stats() == before
    assert len(database.edges_for_target("target")) == 1


def test_replace_file_rejects_exploding_edges_atomically(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SemanticGraphDatabase(tmp_path, directory=tmp_path / "db")
    _seed(database, source)
    before = database.stats()
    node = SemanticNode("new", "function", "new", "new", str(source), 2, 2, ())

    with pytest.raises(ValueError):
        database.replace_file(source, (node,), ExplodingIterable())

    assert database.stats() == before
    assert len(database.edges_for_target("target")) == 1


def test_replace_file_rejects_invalid_record_types_atomically(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SemanticGraphDatabase(tmp_path, directory=tmp_path / "db")
    _seed(database, source)
    before = database.stats()

    with pytest.raises(TypeError):
        database.replace_file(source, (object(),), ())

    assert database.stats() == before
