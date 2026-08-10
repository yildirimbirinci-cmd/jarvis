from __future__ import annotations

from artmach_assistant.core.autonomous_repair_policy import (
    APPROVAL_REQUIRED,
    AUTO_ALLOWED,
    BLOCKED_PROTECTED_TARGET,
    AutonomousRepairDecision,
    validate_runtime_repair_enforcement,
)


def decision(
    *,
    status: str = AUTO_ALLOWED,
    risk: str = "LOW",
    paths: tuple[str, ...] = ("core/helper.py",),
    symbols: tuple[str, ...] = ("Helper.run",),
    max_attempts: int = 3,
) -> AutonomousRepairDecision:
    return AutonomousRepairDecision(
        status=status,
        risk=risk,
        reason="test",
        approved_paths=paths,
        approved_symbols=symbols,
        max_attempts=max_attempts,
    )


def enforce(current: AutonomousRepairDecision, **overrides):
    values = {
        "stored_status": current.status,
        "stored_risk": current.risk,
        "stored_max_attempts": current.max_attempts,
        "approval_granted": True,
        "attempts": 1,
        "session_paths": current.approved_paths,
        "session_symbols": current.approved_symbols,
        "proposal_paths": current.approved_paths,
    }
    values.update(overrides)
    return validate_runtime_repair_enforcement(current, **values)


def test_matching_low_risk_policy_is_allowed() -> None:
    assert enforce(decision()).allowed is True


def test_blocked_current_policy_cannot_apply() -> None:
    current = decision(
        status=BLOCKED_PROTECTED_TARGET,
        risk="CRITICAL",
        max_attempts=0,
    )
    result = enforce(current)
    assert result.allowed is False
    assert "no longer permits" in result.reason


def test_policy_status_tamper_is_blocked() -> None:
    result = enforce(decision(), stored_status=APPROVAL_REQUIRED)
    assert result.allowed is False
    assert "status" in result.reason


def test_policy_risk_tamper_is_blocked() -> None:
    result = enforce(decision(), stored_risk="HIGH")
    assert result.allowed is False
    assert "risk" in result.reason


def test_retry_limit_tamper_is_blocked() -> None:
    result = enforce(decision(max_attempts=2), stored_max_attempts=3)
    assert result.allowed is False
    assert "retry limit" in result.reason


def test_session_path_expansion_is_blocked() -> None:
    result = enforce(
        decision(),
        session_paths=("core/helper.py", "core/other.py"),
    )
    assert result.allowed is False
    assert "paths" in result.reason


def test_session_symbol_expansion_is_blocked() -> None:
    result = enforce(
        decision(),
        session_symbols=("Helper.run", "Other.run"),
    )
    assert result.allowed is False
    assert "symbols" in result.reason


def test_pending_proposal_path_escape_is_blocked() -> None:
    result = enforce(
        decision(),
        proposal_paths=("core/helper.py", "core/autonomous_repair_policy.py"),
    )
    assert result.allowed is False
    assert "exceeds policy path scope" in result.reason


def test_approval_required_without_grant_is_blocked() -> None:
    current = decision(
        status=APPROVAL_REQUIRED,
        risk="MEDIUM",
        max_attempts=2,
    )
    result = enforce(current, approval_granted=False)
    assert result.allowed is False
    assert "approval" in result.reason.casefold()


def test_approval_required_with_grant_is_allowed() -> None:
    current = decision(
        status=APPROVAL_REQUIRED,
        risk="MEDIUM",
        max_attempts=2,
    )
    assert enforce(current, approval_granted=True).allowed is True


def test_zero_attempt_count_is_blocked() -> None:
    result = enforce(decision(), attempts=0)
    assert result.allowed is False
    assert "attempt count" in result.reason


def test_attempt_count_above_limit_is_blocked() -> None:
    result = enforce(decision(max_attempts=2), attempts=3)
    assert result.allowed is False
    assert "attempt count" in result.reason


def test_empty_pending_proposal_scope_is_blocked() -> None:
    result = enforce(decision(), proposal_paths=())
    assert result.allowed is False
    assert "no valid paths" in result.reason
