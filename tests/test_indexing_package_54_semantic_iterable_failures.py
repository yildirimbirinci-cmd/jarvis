from pathlib import Path

import pytest

from indexing.semantic_graph_database import SemanticGraphDatabase


class BrokenIterable:
    def __iter__(self):
        raise RuntimeError("broken")


def test_replace_all_and_records_wrap_broken_iterables(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "a.py"
    source.write_text("pass\n", encoding="utf-8")
    database = SemanticGraphDatabase(project, directory=tmp_path / "storage")

    with pytest.raises(ValueError, match="replacements iterable failed"):
        database.replace_all(BrokenIterable())
    with pytest.raises(ValueError, match="nodes iterable failed"):
        database.replace_file(source, BrokenIterable(), ())
