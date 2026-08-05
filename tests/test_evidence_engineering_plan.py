from __future__ import annotations

from artmach_assistant.core.evidence_conclusion import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    EvidenceConclusion,
)
from artmach_assistant.core.evidence_engineering_plan import (
    PLAN_BLOCKED,
    PLAN_LOCAL_VALIDATION,
    PLAN_PATCH_PROPOSAL,
    build_evidence_engineering_plan,
)


def _conclusion(level: str, score: int) -> EvidenceConclusion:
    return EvidenceConclusion(
        confidence_score=score,
        confidence_level=level,
        accepted_source_count=3,
        rejected_candidate_count=2,
        official_source_count=2,
        unique_host_count=3,
        average_relevance=42,
        conclusion="evidence",
        recommendation="next",
        patch_ready=False,
    )


def test_low_confidence_blocks_patch_planning() -> None:
    plan = build_evidence_engineering_plan(
        _conclusion(CONFIDENCE_LOW, 20),
        title="slow task",
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
    )
    assert plan.status == PLAN_BLOCKED
    assert plan.patch_allowed is False
    assert "kanit boslugunu kapat" in plan.objective


def test_medium_confidence_requires_local_validation() -> None:
    plan = build_evidence_engineering_plan(
        _conclusion(CONFIDENCE_MEDIUM, 60),
        title="slow task",
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
    )
    assert plan.status == PLAN_LOCAL_VALIDATION
    assert any("stage timing" in step for step in plan.steps)
    assert plan.patch_allowed is False


def test_high_confidence_allows_only_patch_proposal() -> None:
    plan = build_evidence_engineering_plan(
        _conclusion(CONFIDENCE_HIGH, 90),
        title="slow task",
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
    )
    assert plan.status == PLAN_PATCH_PROPOSAL
    assert any("action_duration_ms" in step for step in plan.steps)
    assert plan.patch_allowed is False
    assert "Patch izni: hayir" in plan.report()
