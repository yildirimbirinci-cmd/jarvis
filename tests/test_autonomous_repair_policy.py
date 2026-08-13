from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from artmach_assistant.core.autonomous_repair_policy import (
    APPROVAL_REQUIRED,
    AUTO_ALLOWED,
    BLOCKED_HIGH_RISK,
    BLOCKED_INSUFFICIENT_EVIDENCE,
    BLOCKED_NEEDS_FRESH_EVIDENCE,
    BLOCKED_PROTECTED_TARGET,
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
    assert decision.max_attempts == 1
    assert decision.allowed
    assert decision.can_prepare_plan


def test_critical_target_is_hard_blocked() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(severity="critical", affected_paths=("core/task_orchestrator.py",))
    )
    assert decision.status == BLOCKED_HIGH_RISK
    assert decision.risk == "CRITICAL"
    assert decision.max_attempts == 0
    assert not decision.can_prepare_plan


def test_sensitive_target_requires_explicit_approval() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(
            category="repeated_runtime_failure",
            severity="medium",
            affected_paths=("core/network_client.py",),
            affected_symbols=("NetworkClient.request",),
            evidence=(),
        )
    )
    assert decision.status == APPROVAL_REQUIRED
    assert decision.risk == "HIGH"
    assert decision.max_attempts == 1
    assert decision.approval_required
    assert decision.can_prepare_plan
    assert not decision.allowed


def test_high_severity_target_requires_approval() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(
            category="repeated_runtime_failure",
            severity="high",
            affected_paths=("core/assistant.py",),
            affected_symbols=("AssistantEngine.handle",),
            evidence=(),
        )
    )
    assert decision.status == APPROVAL_REQUIRED
    assert decision.risk == "HIGH"
    assert decision.max_attempts == 1


def test_two_file_target_requires_approval_and_single_transformation_limit() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(
            category="repeated_runtime_failure",
            affected_paths=("core/assistant.py", "core/task_orchestrator.py"),
            affected_symbols=("AssistantEngine.handle", "TaskOrchestrator.execute_task"),
            evidence=(),
        )
    )
    assert decision.status == APPROVAL_REQUIRED
    assert decision.risk == "MEDIUM"
    assert decision.max_attempts == 1


def test_protected_policy_target_is_never_autonomous() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(
            category="repeated_runtime_failure",
            affected_paths=("core/autonomous_repair_policy.py",),
            affected_symbols=("assess_autonomous_runtime_repair",),
            evidence=(),
        )
    )
    assert decision.status == BLOCKED_PROTECTED_TARGET
    assert decision.risk == "CRITICAL"
    assert decision.max_attempts == 0


def test_constitution_tree_is_protected() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(
            category="repeated_runtime_failure",
            affected_paths=("core/constitution/runtime_policy.py",),
            affected_symbols=("RuntimePolicy.decide",),
            evidence=(),
        )
    )
    assert decision.status == BLOCKED_PROTECTED_TARGET


def test_protected_symbol_is_blocked_even_outside_protected_path() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(
            category="repeated_runtime_failure",
            affected_paths=("core/helper.py",),
            affected_symbols=("ConstitutionRegistry.register_module",),
            evidence=(),
        )
    )
    assert decision.status == BLOCKED_PROTECTED_TARGET


def test_blocks_weak_or_ambiguous_evidence() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(confidence=0.4, affected_symbols=())
    )
    assert decision.status == BLOCKED_INSUFFICIENT_EVIDENCE
    assert decision.max_attempts == 0


def test_blocks_wrapper_repair_without_fresh_stage_timing() -> None:
    decision = assess_autonomous_runtime_repair(Finding(evidence=()))
    assert decision.status == BLOCKED_NEEDS_FRESH_EVIDENCE
    assert decision.max_attempts == 0


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
    assert decision.max_attempts == 0


def test_more_than_four_production_paths_is_hard_blocked() -> None:
    decision = assess_autonomous_runtime_repair(
        Finding(
            category="repeated_runtime_failure",
            affected_paths=tuple(f"core/module_{index}.py" for index in range(5)),
            affected_symbols=tuple(f"Module{index}.run" for index in range(5)),
            evidence=(),
        )
    )
    assert decision.status == BLOCKED_HIGH_RISK
    assert decision.risk == "CRITICAL"
