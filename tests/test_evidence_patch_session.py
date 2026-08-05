from __future__ import annotations

import pytest

from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPROVAL_PENDING,
    SESSION_APPROVED,
    SESSION_EDIT_PROPOSAL_READY,
    SESSION_HANDOFF_READY,
    SESSION_VALIDATION_PENDING,
    EvidencePatchSession,
    EvidencePatchSessionStore,
)


def test_session_persists_and_transitions(tmp_path) -> None:
    store = EvidencePatchSessionStore(tmp_path / "session.json")
    session = EvidencePatchSession.create(
        proposal_id="PP-ABC",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )
    session = session.transition(SESSION_HANDOFF_READY)
    session = session.transition(SESSION_EDIT_PROPOSAL_READY, edit_summary="proposal ready")
    session = session.transition(SESSION_VALIDATION_PENDING)
    session = session.transition(SESSION_APPROVAL_PENDING, validation_summary="tests passed")
    store.save(session)

    loaded = store.load()
    assert loaded is not None
    assert loaded.status == SESSION_APPROVAL_PENDING
    assert loaded.apply_allowed is False
    assert loaded.validation_summary == "tests passed"


def test_only_explicit_approval_enables_apply() -> None:
    session = EvidencePatchSession.create(
        proposal_id="PP-ABC",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )
    session = session.transition(SESSION_HANDOFF_READY)
    session = session.transition(SESSION_EDIT_PROPOSAL_READY)
    session = session.transition(SESSION_VALIDATION_PENDING)
    session = session.transition(SESSION_APPROVAL_PENDING)
    assert session.apply_allowed is False
    approved = session.transition(SESSION_APPROVED)
    assert approved.apply_allowed is True


def test_invalid_transition_is_rejected() -> None:
    session = EvidencePatchSession.create(
        proposal_id="PP-ABC",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )
    with pytest.raises(ValueError):
        session.transition(SESSION_APPROVED)
