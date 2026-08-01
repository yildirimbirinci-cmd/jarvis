from pathlib import Path
from pkg52.indexing.symbol_graph_update_planner import SymbolGraphUpdatePlanner

class Resolver:
    def affected_files(self, path, **kwargs): return (path,)

def test_removed_file_is_not_rebound_and_noop_is_reported(tmp_path: Path):
    source = tmp_path / 'a.py'; source.write_text('')
    planner = SymbolGraphUpdatePlanner(tmp_path, Resolver())
    assert planner.mark_removed(source) is True
    assert planner.mark_removed(source) is False
    plan = planner.finalize()
    assert plan.changed == (source,)
    assert plan.removed == (source,)
    assert plan.rebind == ()
