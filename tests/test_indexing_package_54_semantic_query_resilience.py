from pathlib import Path

from indexing.semantic_graph_database import SemanticGraphDatabase


class BrokenKinds:
    def __iter__(self):
        raise RuntimeError("broken")


def test_target_query_rejects_broken_kind_iterable_safely(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    database = SemanticGraphDatabase(project, directory=tmp_path / "storage")

    assert database.edges_for_target("target", kinds=BrokenKinds()) == ()
    assert database.edges_for_target(None) == ()
