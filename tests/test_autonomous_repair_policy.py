from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from artmach_assistant.core.autonomous_repair_policy import (
    AUTO_ALLOWED,
    BLOCKED_HIGH_RISK,
    BLOCKED_INSUFFICIENT_EVIDENCE,
    BLOCKED_NEEDS_FRESH_EVIDENCE,
    BLOCKED_WRONG_TARGET,
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
    evidence: tuple[object, ...] = (
        SimpleNamespace(action_duration_ms=10.0, wrapper_overhead_ms=120.0, action_completed=True),
        SimpleNamespace(action_duration_ms=12.0, wrapper_overhead_ms=110.0, action_completed=True),
        SimpleNamespace(action_duration_ms=11.0, wrapper_overhead_ms=115.0, action_completed=True),
    )


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


def test_blocks_wrapper_repair_without_fresh_stage_timing() -> None:
    decision = assess_autonomous_runtime_repair(Finding(evidence=()))
    assert decision.status == BLOCKED_NEEDS_FRESH_EVIDENCE


def test_blocks_wrapper_when_action_is_real_bottleneck() -> None:
    evidence = tuple(
        SimpleNamespace(
            action_duration_ms=500.0,
            wrapper_overhead_ms=5.0,
            action_completed=True,
        )
        for _ in range(3)
    )
    decision = assess_autonomous_runtime_repair(Finding(evidence=evidence))
    assert decision.status == BLOCKED_WRONG_TARGET
