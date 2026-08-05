from __future__ import annotations

from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPLIED,
    SESSION_APPLYING,
    SESSION_APPROVED,
    SESSION_FAILED,
    EvidencePatchSession,
)


def _approved() -> EvidencePatchSession:
    session = EvidencePatchSession.create(
        proposal_id="PP-APPLY",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )
    session = session.transition("HANDOFF_READY")
    session = session.transition("EDIT_PROPOSAL_READY")
    session = session.transition("VALIDATION_PENDING")
    session = session.transition("APPROVAL_PENDING")
    return session.transition(SESSION_APPROVED)


def test_apply_session_records_success_metadata() -> None:
    session = _approved().transition(
        SESSION_APPLYING,
        apply_summary="apply started",
    )
    assert session.apply_allowed is True

    session = session.transition(
        SESSION_APPLIED,
        apply_summary="2 files applied",
        version_summary="version 42",
    )

    assert session.status == SESSION_APPLIED
    assert session.apply_allowed is False
    assert session.apply_summary == "2 files applied"
    assert session.version_summary == "version 42"


def test_apply_session_records_failed_rollback() -> None:
    session = _approved().transition(SESSION_APPLYING)
    session = session.transition(
        SESSION_FAILED,
        apply_summary="tests failed",
        rollback_summary="checkpoint restored",
        error="new regression",
    )

    assert session.status == SESSION_FAILED
    assert session.apply_allowed is False
    assert session.rollback_summary == "checkpoint restored"
    assert session.error == "new regression"
