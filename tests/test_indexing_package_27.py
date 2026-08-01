from pathlib import Path

import pytest

from artmach_assistant.indexing.semantic_graph import SemanticGraph
from artmach_assistant.indexing.semantic_graph_builder import SemanticEdge, SemanticNode
from artmach_assistant.indexing.semantic_graph_database import SemanticGraphDatabase


def test_semantic_graph_accepts_single_suffix_string(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("value = helper()\n", encoding="utf-8")
    graph = SemanticGraph(tmp_path, suffixes="py")

    result = graph.update_file(source)

    assert result is not None
    assert result.path == str(source.resolve())


def test_semantic_graph_rejects_empty_suffixes(tmp_path: Path):
    with pytest.raises(ValueError):
        SemanticGraph(tmp_path, suffixes=[])


def test_database_treats_single_kind_string_as_one_kind(tmp_path: Path):
    source = tmp_path / "sample.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SemanticGraphDatabase(tmp_path, directory=tmp_path / "db")
    node = SemanticNode("node", "function", "caller", "caller", str(source), 1, 1, ())
    edges = (
        SemanticEdge("node", "target", "call", str(source), 1, ()),
        SemanticEdge("node", "target", "read", str(source), 1, ()),
    )
    database.replace_file(source, (node,), edges)

    found = database.edges_for_target("target", kinds="call")

    assert tuple(edge.kind for edge in found) == ("call",)


def test_database_rejects_non_iterable_kinds(tmp_path: Path):
    database = SemanticGraphDatabase(tmp_path, directory=tmp_path / "db")
    with pytest.raises(TypeError):
        database.edges_for_target("target", kinds=None)
