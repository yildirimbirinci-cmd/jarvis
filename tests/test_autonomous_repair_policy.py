from __future__ import annotations

from dataclasses import dataclass

from artmach_assistant.core.autonomous_repair_policy import (
    AUTO_ALLOWED,
    BLOCKED_HIGH_RISK,
    BLOCKED_INSUFFICIENT_EVIDENCE,
    assess_autonomous_runtime_repair,
)


@dataclass
class Finding:
    category: str = "repeated_slow_operation"
    severity: str = "medium"
    confidence: float = 0.92
    occurrence_count: int = 8
    affected_paths: tuple[str, ...] = ("core/task_orchestrator.py",)
    affected_symbols: tuple[str, ...] = ("TaskOrchestrator.wrap.execute",)


def test_allows_exact_low_risk_runtime_target() -> None:
    decision = assess_autonomous_runtime_repair(Finding())
    assert decision.status == AUTO_ALLOWED
    assert decision.risk == "LOW"


def test_blocks_sensitive_or_critical_target() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(
            severity="critical",
            affected_paths=("core/security_guard.py",),
        )
    )
    assert decision.status == BLOCKED_HIGH_RISK
    assert decision.risk == "HIGH"


def test_blocks_weak_or_ambiguous_evidence() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(confidence=0.4, affected_symbols=())
    )
    assert decision.status == BLOCKED_INSUFFICIENT_EVIDENCE
