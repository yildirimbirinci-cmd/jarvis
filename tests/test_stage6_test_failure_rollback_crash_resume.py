from __future__ import annotations

from pathlib import Path


def _source() -> str:
    return Path("core/assistant.py").read_text(encoding="utf-8")


def test_test_failure_rollback_enters_restart_safe_state_before_undo():
    source = _source()
    marker = '"rolling_back",\n                "Yeni regresyon algılandı; doğrulanmış baseline geri yükleniyor."'
    assert marker in source
    rolling = source.index(marker)
    undo = source.index("self.own_code_transactions.undo()", rolling)
    assert rolling < undo


def test_failed_rollback_requires_recovery_instead_of_claiming_success():
    source = _source()
    assert '"recovery_required",\n                    f"Test-failure rollback tamamlanamadı:' in source
    assert "rollback sonrası baseline doğrulaması tamamlanamadı" in source


def test_successful_test_failure_rollback_revalidates_baseline():
    source = _source()
    branch = source[source.index('"Yeni regresyon algılandı; doğrulanmış baseline geri yükleniyor."'):]
    assert "_validate_recovered_engineering_source(rollback_cycle)" in branch
    assert "Rollback sonrası derleme, temiz süreç ve regresyon" in branch


def test_restart_recovery_recognizes_rolling_back_state():
    source = _source()
    assert '"applying", "validating", "rolling_back"' in source
    assert '"recovery_required"' in source
