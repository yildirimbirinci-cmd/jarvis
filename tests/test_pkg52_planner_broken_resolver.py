from pathlib import Path
from pkg52.indexing.symbol_graph_update_planner import SymbolGraphUpdatePlanner

class Resolver:
    def affected_files(self, path, **kwargs):
        def broken():
            yield path
            raise RuntimeError('boom')
        return broken()

def test_broken_resolver_iterable_does_not_leak_partial_impact(tmp_path: Path):
    source = tmp_path / 'a.py'; source.write_text('')
    planner = SymbolGraphUpdatePlanner(tmp_path, Resolver())
    assert planner.capture_before(source) is True
    assert planner.pending_snapshot()['pre_impact'] == []
