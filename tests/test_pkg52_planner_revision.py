from pathlib import Path
from pkg52.indexing.symbol_graph_update_planner import SymbolGraphUpdatePlanner

class Resolver:
    def affected_files(self, path, **kwargs):
        return (path,)

def test_revision_only_changes_for_non_empty_finalize(tmp_path: Path):
    source = tmp_path / 'a.py'; source.write_text('x=1\n')
    planner = SymbolGraphUpdatePlanner(tmp_path, Resolver())
    assert planner.finalize().snapshot() == {'changed': [], 'removed': [], 'rebind': []}
    assert planner.revision == 0
    assert planner.capture_before(source) is True
    planner.finalize()
    assert planner.revision == 1
