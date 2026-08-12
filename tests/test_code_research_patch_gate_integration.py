from artmach_assistant.core.evidence_conclusion import CONFIDENCE_HIGH, EvidenceConclusion
from artmach_assistant.core.evidence_engineering_plan import PLAN_PATCH_PROPOSAL, EvidenceEngineeringPlan
from artmach_assistant.core.evidence_patch_proposal import (
    PROPOSAL_BLOCKED,
    PROPOSAL_READY_FOR_REVIEW,
    build_guarded_evidence_patch_proposal,
)


def _plan(status=PLAN_PATCH_PROPOSAL, allowed=True):
    return EvidenceEngineeringPlan(
        status=status,
        objective='objective',
        rationale='reason',
        steps=('step',),
        acceptance_criteria=('test',),
        safety_constraints=('safe',),
        patch_allowed=allowed,
    )


def _conclusion(patch_ready=True):
    return EvidenceConclusion(
        confidence_score=90,
        confidence_level=CONFIDENCE_HIGH,
        accepted_source_count=2,
        rejected_candidate_count=0,
        official_source_count=1,
        unique_host_count=2,
        average_relevance=45,
        conclusion='supported',
        recommendation='validate locally',
        patch_ready=patch_ready,
    )


def test_unresolved_target_is_blocked_even_when_old_flags_are_ready():
    proposal = build_guarded_evidence_patch_proposal(_plan(), _conclusion(), path='', symbol='')
    assert proposal.status == PROPOSAL_BLOCKED


def test_resolved_target_and_completed_local_validation_can_reach_review():
    proposal = build_guarded_evidence_patch_proposal(
        _plan(), _conclusion(), path='core/example.py', symbol='Example.run'
    )
    assert proposal.status == PROPOSAL_READY_FOR_REVIEW


def test_patch_ready_false_remains_blocked():
    proposal = build_guarded_evidence_patch_proposal(
        _plan(), _conclusion(False), path='core/example.py', symbol='Example.run'
    )
    assert proposal.status == PROPOSAL_BLOCKED
