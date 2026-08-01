from pathlib import Path
from pkg52.indexing.symbol_graph_update_planner import SymbolGraphUpdatePlanner

class Resolver:
    def affected_files(self, path, **kwargs): return ()

def test_outside_root_is_rejected_without_state_change(tmp_path: Path):
    planner = SymbolGraphUpdatePlanner(tmp_path, Resolver())
    outside = tmp_path.parent / 'outside.py'
    assert planner.capture_before(outside) is False
    assert planner.mark_removed(outside) is False
    assert planner.pending_snapshot() == {'changed': [], 'removed': [], 'pre_impact': []}
