from pathlib import Path

from indexing.semantic_graph_builder import SemanticEdge, SemanticNode
from indexing.semantic_graph_database import SemanticGraphDatabase


def test_snapshot_counts_edge_only_files_and_uses_one_revision(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "edge_only.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SemanticGraphDatabase(project, directory=tmp_path / "storage")
    edge = SemanticEdge("file:test", "target", "calls", str(source), 1)

    assert database.replace_file(source, (), (edge,)) is True
    snapshot = database.snapshot()

    assert snapshot["files"] == [str(source.resolve())]
    assert snapshot["stats"]["semantic_files"] == 1
    assert snapshot["revision"] == snapshot["stats"]["semantic_revision"]
