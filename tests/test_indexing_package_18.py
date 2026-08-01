from pathlib import Path
from pkg18work.indexing.call_graph.graph import CallGraph


def test_remove_file_rejects_invalid_and_outside_paths(tmp_path: Path):
    graph = CallGraph(tmp_path)
    assert graph.remove_file(object()) is False
    assert graph.remove_file(tmp_path.parent / "outside.py") is False


def test_reachability_handles_failing_entry_point_iterable(tmp_path: Path):
    graph = CallGraph(tmp_path)
    def broken():
        yield "main"
        raise RuntimeError("boom")
    report = graph.reachability_report(broken())
    assert report.entry_points == ()
    assert report.reachable_symbols == ()
