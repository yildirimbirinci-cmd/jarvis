from __future__ import annotations

from pathlib import Path


def _assistant_source() -> str:
    return Path("core/assistant.py").read_text(encoding="utf-8")


def test_interrupted_apply_requires_recovery_before_new_apply() -> None:
    source = _assistant_source()
    assert '"applying", "validating", "rolling_back"' in source
    assert '"recovery_required"' in source
    assert "otomatik yeni" in source and "apply engellendi" in source


def test_recovery_verifies_git_source_compile_runtime_and_tests() -> None:
    source = _assistant_source()
    assert "_verify_interrupted_engineering_recovery" in source
    assert "_validate_recovered_engineering_source" in source
    assert "_compile_own_code()" in source
    assert "_runtime_health_check()" in source
    assert "_run_own_tests()" in source
    assert '"recovered"' in source


def test_dirty_incomplete_apply_is_not_declared_recovered_without_rollback() -> None:
    source = _assistant_source()
    assert "recover_incomplete" in source
    assert "transactions.undo()" in source
    assert "Transaction recovery sonrası Git çalışma ağacı temiz değil" in source
