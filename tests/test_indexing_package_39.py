from __future__ import annotations

from pathlib import Path

from pkg39work.indexing.call_graph.graph import CallGraph
from pkg39work.indexing.call_graph.model import CallGraphBuildResult


class _MemoryFailingIterable:
    def __iter__(self):
        raise MemoryError("simulated allocation failure")


class _MemoryFailingDict(dict):
    def items(self):
        raise MemoryError("simulated snapshot failure")


def _result(path: Path) -> CallGraphBuildResult:
    return CallGraphBuildResult(path=str(path), call_sites=(), edges=())


def test_replace_files_rejects_memory_failing_iterable_atomically(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    graph = CallGraph(tmp_path)
    assert graph.replace_file(_result(source)) is True
    before = graph.snapshot()

    assert graph.replace_files(_MemoryFailingIterable()) is False
    assert graph.snapshot() == before


def test_load_snapshot_rejects_memory_failing_edge_mapping_atomically(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    graph = CallGraph(tmp_path)
    assert graph.replace_file(_result(source)) is True
    before = graph.snapshot()

    payload = {
        "revision": 99,
        "edges_by_file": _MemoryFailingDict(),
        "diagnostics_by_file": {},
    }
    assert graph.load_snapshot(payload) is False
    assert graph.snapshot() == before


def test_load_snapshot_rejects_memory_failing_diagnostics_mapping_atomically(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    graph = CallGraph(tmp_path)
    assert graph.replace_file(_result(source)) is True
    before = graph.snapshot()

    payload = {
        "revision": 99,
        "edges_by_file": {},
        "diagnostics_by_file": _MemoryFailingDict(),
    }
    assert graph.load_snapshot(payload) is False
    assert graph.snapshot() == before
