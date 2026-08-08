from pathlib import Path

def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core" / "assistant.py").read_text(encoding="utf-8")

def test_unversioned_lost_proposal_keeps_safe_legacy_message():
    source = _source()
    start = source.index("def own_code_cycle_report")
    end = source.index("\n    def _own_code_cycle_request", start)
    block = source[start:end]
    assert 'elif stage == "proposal_ready" and pending is None:' in block
    assert "bellekte tutulmamış" in block

def test_v3_persisted_proposal_still_reconciles_to_stale():
    source = _source()
    start = source.index("def own_code_cycle_report")
    end = source.index("\n    def _own_code_cycle_request", start)
    block = source[start:end]
    assert 'int(cycle.get("version", 0) or 0) == 3' in block
    assert '"stale"' in block
    assert "cycle = self._load_own_code_cycle() or cycle" in block
