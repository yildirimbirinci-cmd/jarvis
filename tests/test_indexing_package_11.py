from pathlib import Path

import pytest

from artmach_assistant.indexing.semantic_graph import SemanticGraph
from artmach_assistant.indexing.semantic_graph_builder import SemanticEdge, SemanticNode
from artmach_assistant.indexing.semantic_graph_database import SemanticGraphDatabase


def test_semantic_graph_rebuild_accepts_single_string_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    source = root / "sample.py"
    source.write_text("def target():\n    return 1\n", encoding="utf-8")

    database_dir = tmp_path / "graphs"
    monkeypatch.setattr("artmach_assistant.indexing.semantic_graph_database.DATA_DIR", tmp_path)
    graph = SemanticGraph(root)
    results = graph.rebuild(str(source))

    assert len(results) == 1
    assert results[0].path == str(source.resolve())
    assert graph.stats()["semantic_files"] == 1


def test_database_accepts_string_directory_and_rejects_external_paths(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    database = SemanticGraphDatabase(root, directory=str(tmp_path / "graphs"))
    source = root / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    node = SemanticNode("node:1", "file", "sample.py", "sample", str(source), 1, 1)

    database.replace_file(source, (node,), ())
    assert database.stats()["semantic_nodes"] == 1

    outside = tmp_path / "outside.py"
    with pytest.raises(ValueError):
        database.replace_file(outside, (), ())
    with pytest.raises(ValueError):
        database.remove_file(outside)


def test_invalid_limits_and_corrupt_metadata_do_not_crash(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    database = SemanticGraphDatabase(root, directory=tmp_path / "graphs")
    source = root / "sample.py"
    source.write_text("target()\n", encoding="utf-8")
    node = SemanticNode("node:1", "file", "sample.py", "sample", str(source), 1, 1)
    edge = SemanticEdge("node:1", "target", "calls", str(source), 1)
    database.replace_file(source, (node,), (edge,))

    assert len(database.edges_for_target("target", limit="invalid")) == 1
    assert len(database.edges_for_target("target", limit=float("inf"))) == 1

    with database._connect() as connection:
        connection.execute("UPDATE semantic_edges SET metadata = 'not-json'")
    result = database.edges_for_target("target")
    assert result[0].metadata == ()


def test_database_rejects_empty_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(ValueError):
        SemanticGraphDatabase(root, directory="   ")
