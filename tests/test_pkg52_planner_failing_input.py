from pathlib import Path
from pkg52.indexing.symbol_graph_update_planner import SymbolGraphUpdatePlanner

class Resolver:
    def affected_files(self, path, **kwargs): return ()

def test_failing_input_iterable_is_atomic(tmp_path: Path):
    source = tmp_path / 'a.py'; source.write_text('')
    planner = SymbolGraphUpdatePlanner(tmp_path, Resolver())
    planner.capture_before(source)
    before = planner.pending_snapshot()
    def broken():
        yield tmp_path / 'b.py'
        raise RuntimeError('boom')
    assert planner.capture_before(broken()) is False
    assert planner.pending_snapshot() == before
