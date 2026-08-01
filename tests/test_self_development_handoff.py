from __future__ import annotations

import json

from artmach_assistant.core.self_development_cli import SelfDevelopmentResult
from artmach_assistant.core.self_development_gate import GateCheck, GateResult
from artmach_assistant.core.self_development_handoff import run_handoff, write_handoff_report


def _gate(ready: bool) -> GateResult:
    return GateResult(ready, (GateCheck("test", ready, "ok" if ready else "blocked"),))


def test_handoff_is_blocked_when_gate_fails() -> None:
    called = False

    def runner(instruction: str) -> SelfDevelopmentResult:
        nonlocal called
        called = True
        return SelfDevelopmentResult("applied", 0, instruction)

    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(False),
        development_runner=runner,
        acknowledged=True,
    )
    assert result.exit_code == 1
    assert not result.attempted
    assert not called


def test_handoff_requires_explicit_acknowledgement() -> None:
    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(True),
        development_runner=lambda instruction: SelfDevelopmentResult("applied", 0, instruction),
        acknowledged=False,
    )
    assert result.exit_code == 2
    assert not result.attempted
    assert "NOT ATTEMPTED" in result.report()


def test_successful_apply_is_reported_as_success(tmp_path) -> None:
    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(True),
        development_runner=lambda instruction: SelfDevelopmentResult("applied", 0, instruction),
        acknowledged=True,
    )
    assert result.succeeded
    assert result.exit_code == 0
    path = write_handoff_report(result, tmp_path / "handoff.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["succeeded"] is True
    assert payload["development"]["stage"] == "applied"


def test_non_applied_zero_exit_result_does_not_count_as_success() -> None:
    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(True),
        development_runner=lambda instruction: SelfDevelopmentResult("proposal", 0, instruction),
        acknowledged=True,
    )
    assert not result.succeeded
    assert result.exit_code == 1


def test_failed_change_audit_blocks_success() -> None:
    from artmach_assistant.core.self_development_audit import ChangeAudit

    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(True),
        development_runner=lambda instruction: SelfDevelopmentResult("applied", 0, instruction),
        acknowledged=True,
        audit_factory=lambda: ChangeAudit(False, ("core/a.py",), 1, 0, "", "blocked"),
    )
    assert not result.succeeded
    assert result.exit_code == 1
    assert "CHANGE AUDIT: FAIL" in result.report()


def test_successful_change_audit_is_included_in_report(tmp_path) -> None:
    from artmach_assistant.core.self_development_audit import ChangeAudit

    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(True),
        development_runner=lambda instruction: SelfDevelopmentResult("applied", 0, instruction),
        acknowledged=True,
        audit_factory=lambda: ChangeAudit(True, ("core/a.py",), 2, 1, "abc", "ok"),
    )
    path = write_handoff_report(result, tmp_path / "handoff-audit.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["succeeded"] is True
    assert payload["audit"]["patch_sha256"] == "abc"


def test_failed_audit_triggers_automatic_rollback() -> None:
    from artmach_assistant.core.self_development_audit import ChangeAudit, RollbackAudit

    called: list[tuple[str, ...]] = []

    def rollback(paths: tuple[str, ...]) -> RollbackAudit:
        called.append(paths)
        return RollbackAudit(True, paths, "restored")

    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(True),
        development_runner=lambda instruction: SelfDevelopmentResult("applied", 0, instruction),
        acknowledged=True,
        audit_factory=lambda: ChangeAudit(False, ("core/a.py",), 1, 0, "", "blocked"),
        rollback_runner=rollback,
    )

    assert called == [("core/a.py",)]
    assert not result.succeeded
    assert result.rollback is not None and result.rollback.ok
    assert "AUTOMATIC ROLLBACK: SUCCESS" in result.report()


def test_successful_audit_does_not_trigger_rollback() -> None:
    from artmach_assistant.core.self_development_audit import ChangeAudit, RollbackAudit

    called = False

    def rollback(paths: tuple[str, ...]) -> RollbackAudit:
        nonlocal called
        called = True
        return RollbackAudit(True, paths, "restored")

    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(True),
        development_runner=lambda instruction: SelfDevelopmentResult("applied", 0, instruction),
        acknowledged=True,
        audit_factory=lambda: ChangeAudit(True, ("core/a.py",), 1, 0, "abc", "ok"),
        rollback_runner=rollback,
    )

    assert result.succeeded
    assert not called
    assert result.rollback is None


def test_failed_development_with_residual_change_triggers_rollback() -> None:
    from artmach_assistant.core.self_development_audit import ChangeAudit, RollbackAudit

    called: list[tuple[str, ...]] = []

    def rollback(paths: tuple[str, ...]) -> RollbackAudit:
        called.append(paths)
        return RollbackAudit(True, paths, "restored")

    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(True),
        development_runner=lambda instruction: SelfDevelopmentResult("failed", 7, instruction),
        acknowledged=True,
        audit_factory=lambda: ChangeAudit(True, ("core/a.py",), 1, 0, "abc", "ok"),
        rollback_runner=rollback,
    )

    assert called == [("core/a.py",)]
    assert not result.succeeded
    assert result.rollback is not None and result.rollback.ok


def test_failed_development_without_change_does_not_call_rollback() -> None:
    from artmach_assistant.core.self_development_audit import ChangeAudit, RollbackAudit

    called = False

    def rollback(paths: tuple[str, ...]) -> RollbackAudit:
        nonlocal called
        called = True
        return RollbackAudit(True, paths, "restored")

    result = run_handoff(
        "küçük düzeltme",
        gate_factory=lambda: _gate(True),
        development_runner=lambda instruction: SelfDevelopmentResult("failed", 7, instruction),
        acknowledged=True,
        audit_factory=lambda: ChangeAudit(False, (), 0, 0, "", "no source change was produced"),
        rollback_runner=rollback,
    )

    assert not called
    assert result.rollback is None
