from pathlib import Path


def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core" / "assistant.py").read_text(encoding="utf-8")


def test_legacy_proposal_ready_is_reconciled_after_restart():
    source = _source()
    start = source.index("def own_code_cycle_report")
    end = source.index("\n    def _own_code_cycle_request", start)
    block = source[start:end]

    assert "legacy_pending_without_proposal = (" in block
    assert 'int(cycle.get("version", 0) or 0) == 3' in block
    assert 'stage == "proposal_ready"' in block
    assert "pending is None" in block
    assert 'self._save_own_code_cycle(' in block
    assert '"stale"' in block
    assert "cycle = self._load_own_code_cycle() or cycle" in block


def test_stale_state_has_deterministic_status_label():
    source = _source()
    start = source.index("def own_code_cycle_report")
    end = source.index("\n    def _own_code_cycle_request", start)
    block = source[start:end]

    assert (
        '"stale": "restart sonrası eski taslak kaydı geçersizleştirildi"'
        in block
    )


def test_v4_completed_persistence_remains_present():
    source = _source()

    assert '"version": 4' in source
    assert "changed_paths=completed_paths" in source
    assert "validation_summary=(" in source
    assert "version_summary=version_report" in source
