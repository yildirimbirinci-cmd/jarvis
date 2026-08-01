import json
from pathlib import Path
from pkg52.indexing.symbol_graph_update_planner import SymbolGraphUpdatePlanner

class Resolver:
    def affected_files(self, path, **kwargs): return ()

def test_pending_snapshot_is_deterministic_and_json_native(tmp_path: Path):
    b = tmp_path / 'b.py'; a = tmp_path / 'a.py'
    a.write_text(''); b.write_text('')
    planner = SymbolGraphUpdatePlanner(tmp_path, Resolver())
    planner.capture_before((b, a))
    payload = planner.pending_snapshot()
    assert payload['changed'] == [str(a), str(b)]
    json.dumps(payload)
