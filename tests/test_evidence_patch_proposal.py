from __future__ import annotations

from artmach_assistant.core.evidence_conclusion import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    EvidenceConclusion,
)
from artmach_assistant.core.evidence_engineering_plan import (
    PLAN_LOCAL_VALIDATION,
    PLAN_PATCH_PROPOSAL,
    EvidenceEngineeringPlan,
)
from artmach_assistant.core.evidence_patch_proposal import (
    PROPOSAL_BLOCKED,
    PROPOSAL_READY_FOR_REVIEW,
    build_evidence_patch_proposal,
)


def _conclusion(level: str, *, patch_ready: bool = False) -> EvidenceConclusion:
    return EvidenceConclusion(90 if level == CONFIDENCE_HIGH else 60, level, 3, 1, 2, 3, 42, "ok", "next", patch_ready)


def _plan(status: str, *, patch_allowed: bool = False) -> EvidenceEngineeringPlan:
    return EvidenceEngineeringPlan(status, "dar patch", "reason", ("measure", "change"), ("tests pass",), ("approval",), patch_allowed)


def test_high_confidence_without_local_readiness_blocks_proposal() -> None:
    proposal = build_evidence_patch_proposal(_plan(PLAN_PATCH_PROPOSAL), _conclusion(CONFIDENCE_HIGH), path="core/task_orchestrator.py", symbol="TaskOrchestrator.wrap.execute")
    assert proposal.status == PROPOSAL_BLOCKED
    assert proposal.change_scope == ()


def test_explicit_local_readiness_creates_review_only_proposal() -> None:
    proposal = build_evidence_patch_proposal(_plan(PLAN_PATCH_PROPOSAL, patch_allowed=True), _conclusion(CONFIDENCE_HIGH, patch_ready=True), path="core/task_orchestrator.py", symbol="TaskOrchestrator.wrap.execute")
    assert proposal.status == PROPOSAL_READY_FOR_REVIEW
    assert proposal.proposal_id.startswith("PP-")
    assert proposal.user_approval_required is True
    assert proposal.apply_allowed is False
    assert proposal.change_scope == ("measure", "change")


def test_medium_confidence_blocks_proposal() -> None:
    proposal = build_evidence_patch_proposal(_plan(PLAN_LOCAL_VALIDATION), _conclusion(CONFIDENCE_MEDIUM), path="core/task_orchestrator.py", symbol="TaskOrchestrator.wrap.execute")
    assert proposal.status == PROPOSAL_BLOCKED
    assert proposal.change_scope == ()
    assert "Uygulama izni: hayir" in proposal.report()
