from pathlib import Path

import pytest

from indexing.semantic_graph_builder import SemanticNode
from indexing.semantic_graph_database import SemanticGraphDatabase


def test_replace_all_rejects_duplicate_node_ids_across_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first = project / "a.py"
    second = project / "b.py"
    first.write_text("pass\n", encoding="utf-8")
    second.write_text("pass\n", encoding="utf-8")
    database = SemanticGraphDatabase(project, directory=tmp_path / "storage")
    node_a = SemanticNode("same", "file", "a", "a", str(first), 1, 1)
    node_b = SemanticNode("same", "file", "b", "b", str(second), 1, 1)

    with pytest.raises(ValueError, match="unique"):
        database.replace_all(((first, (node_a,), ()), (second, (node_b,), ())))

    assert database.stats()["semantic_nodes"] == 0
