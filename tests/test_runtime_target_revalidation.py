from __future__ import annotations

from dataclasses import replace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.autonomous_repair_policy import (
    AUTO_ALLOWED,
    BLOCKED_WRONG_TARGET,
)
from artmach_assistant.core.runtime_observability import RuntimeEvidence, RuntimeFinding


def _finding(*, path: str, symbol: str) -> RuntimeFinding:
    evidence = tuple(
        RuntimeEvidence(
            event_id=f"e{index}",
            created_at=f"2026-08-07T10:0{index}:00+00:00",
            detail="timed task",
            duration_ms=121.0,
            source_path="core/task_orchestrator.py",
            symbol="TaskOrchestrator.wrap.execute",
            action_duration_ms=120.0,
            wrapper_overhead_ms=1.0,
            action_started=True,
            action_completed=True,
        )
        for index in range(3)
    )
    return RuntimeFinding(
        finding_id="RUN-06578E9EDE",
        severity="medium",
        category="repeated_slow_operation",
        title="Repeated slow operation",
        explanation="Timing evidence",
        confidence=0.95,
        occurrence_count=5,
        last_seen="2026-08-07T10:02:00+00:00",
        workspace="",
        scope="own_code",
        affected_paths=(path,),
        affected_symbols=(symbol,),
        evidence=evidence,
        recommendation="Validate target",
        acceptance_criteria=("No regression",),
        research_query="",
    )


def test_wrong_wrapper_target_is_revalidated_before_policy_block() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    stale = _finding(
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
    )
    promoted = replace(
        stale,
        affected_paths=("core/assistant.py",),
        affected_symbols=("AssistantEngine.handle",),
    )
    calls: list[str] = []
    engine._runtime_finding_local_validation = lambda finding: calls.append(
        finding.finding_id
    ) or "LOCAL VALIDATION"
    engine._find_runtime_finding = lambda finding_id: promoted

    refreshed, decision, report = engine._assess_runtime_repair_with_target_refresh(stale)

    assert calls == ["RUN-06578E9EDE"]
    assert refreshed.affected_paths == ("core/assistant.py",)
    assert refreshed.affected_symbols == ("AssistantEngine.handle",)
    assert decision.status == AUTO_ALLOWED
    assert report == "LOCAL VALIDATION"


def test_wrong_target_stays_blocked_when_local_validation_cannot_promote() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    stale = _finding(
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
    )
    engine._runtime_finding_local_validation = lambda finding: "INSUFFICIENT"
    engine._find_runtime_finding = lambda finding_id: stale

    refreshed, decision, report = engine._assess_runtime_repair_with_target_refresh(stale)

    assert refreshed is stale
    assert decision.status == BLOCKED_WRONG_TARGET
    assert report == "INSUFFICIENT"
