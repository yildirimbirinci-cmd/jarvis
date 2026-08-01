from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.own_code_readiness import (
    REQUIRED_MODULES,
    assess_readiness,
)


class _Integrity:
    def __init__(self, valid: bool):
        self.valid = valid

    def report(self) -> str:
        return "günlük sağlam" if self.valid else "günlük bozuk"


def _write_required(root) -> None:
    for relative in REQUIRED_MODULES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# module\n", encoding="utf-8")


def test_all_final_checks_produce_ready_report(tmp_path) -> None:
    _write_required(tmp_path)
    result = assess_readiness(
        tmp_path,
        SimpleNamespace(verify=lambda: _Integrity(True)),
        SimpleNamespace(incomplete_count=lambda: 0),
        validation_success=True,
    )
    assert result.ready
    assert "KABULÜ: HAZIR" in result.report()
    assert result.report().count("GEÇTİ") == 4


def test_missing_module_blocks_readiness(tmp_path) -> None:
    result = assess_readiness(
        tmp_path,
        SimpleNamespace(verify=lambda: _Integrity(True)),
        SimpleNamespace(incomplete_count=lambda: 0),
        validation_success=True,
    )
    assert not result.ready
    assert "eksik:" in result.report()


def test_corrupt_audit_or_incomplete_checkpoint_blocks_readiness(tmp_path) -> None:
    _write_required(tmp_path)
    result = assess_readiness(
        tmp_path,
        SimpleNamespace(verify=lambda: _Integrity(False)),
        SimpleNamespace(incomplete_count=lambda: 1),
        validation_success=True,
    )
    assert not result.ready
    assert "günlük bozuk" in result.report()
    assert "1 yarım işlem" in result.report()


def test_failed_full_validation_blocks_readiness(tmp_path) -> None:
    _write_required(tmp_path)
    result = assess_readiness(
        tmp_path,
        SimpleNamespace(verify=lambda: _Integrity(True)),
        SimpleNamespace(incomplete_count=lambda: 0),
        validation_success=False,
    )
    assert not result.ready
    assert "son tam doğrulama başarısız" in result.report()
