from pathlib import Path


def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "core" / "assistant.py").read_text(encoding="utf-8")


def test_rollback_paths_are_captured_from_approved_proposal():
    source = _source()
    assert "rollback_paths = (" in source
    assert "for change in approved_proposal.files" in source


def test_regression_rollback_persists_restart_safe_details():
    source = _source()
    rolling_marker = (
        '"rolling_back",\n'
        '                "Yeni regresyon algılandı; doğrulanmış baseline geri yükleniyor."'
    )
    rolled_back_marker = (
        '"rolled_back", failure_summary,'
    )

    rolling_pos = source.index(rolling_marker)
    rolled_back_pos = source.index(rolled_back_marker, rolling_pos)
    block = source[rolling_pos:rolled_back_pos + 1200]

    assert "changed_paths=rollback_paths" in block
    assert "_validate_recovered_engineering_source(rollback_cycle)" in block
    assert '"recovery_required"' in block
    assert "version_summary=str(rollback)[:3000]" in block
    assert '"rolled_back"' in block


def test_all_automatic_validation_rollbacks_persist_paths():
    source = _source()
    assert source.count("changed_paths=rollback_paths") >= 5
    assert source.count("version_summary=str(rollback)[:3000]") >= 5


def test_completed_v4_state_remains_supported():
    source = _source()
    assert '"version": 4' in source
    assert "changed_paths=completed_paths" in source
