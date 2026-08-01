from pathlib import Path

import pytest

from indexing.semantic_graph import SemanticGraph


class BrokenPaths:
    def __iter__(self):
        raise RuntimeError("broken")


def test_facade_validates_suffixes_and_broken_rebuild_iterable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(ValueError, match="source suffix"):
        SemanticGraph(project, suffixes=())

    graph = SemanticGraph(project)
    with pytest.raises(ValueError, match="paths iterable failed"):
        graph.rebuild(BrokenPaths())
    assert graph.remove_file(tmp_path / "outside.py") is False
