from pathlib import Path

from indexing.call_graph.graph import CallGraph
from indexing.call_graph.model import CallGraphBuildResult


def test_replace_files_rejects_scalar_iterables(tmp_path: Path) -> None:
    graph = CallGraph(tmp_path)
    assert graph.replace_files("not-a-batch") is False
    assert graph.stats()["call_graph_revision"] == 0


def test_replace_files_handles_failing_generator_atomically(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("pass\n", encoding="utf-8")
    graph = CallGraph(tmp_path)

    def broken_batch():
        yield CallGraphBuildResult(str(source), (), ())
        raise RuntimeError("watcher failed")

    assert graph.replace_files(broken_batch()) is False
    assert graph.edges_for_file(source) == ()
    assert graph.stats()["call_graph_revision"] == 0


def test_clear_resets_graph_once(tmp_path: Path) -> None:
    graph = CallGraph(tmp_path)
    graph.clear()
    assert graph.stats()["call_graph_revision"] == 1
