from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPROVAL_PENDING,
    SESSION_APPROVED,
    SESSION_EDIT_PROPOSAL_READY,
    SESSION_HANDOFF_READY,
    EvidencePatchSession,
    EvidencePatchSessionStore,
)


def _ready_session(store: EvidencePatchSessionStore) -> EvidencePatchSession:
    session = EvidencePatchSession.create(
        proposal_id="PP-VALIDATE",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )
    session = session.transition(SESSION_HANDOFF_READY)
    session = session.transition(SESSION_EDIT_PROPOSAL_READY, edit_summary="ready")
    store.save(session)
    return session


def test_session_persists_worktree_and_test_summaries(tmp_path: Path) -> None:
    store = EvidencePatchSessionStore(tmp_path / "session.json")
    session = _ready_session(store)
    session = session.transition("VALIDATION_PENDING")
    session = session.transition(
        SESSION_APPROVAL_PENDING,
        validation_summary="ok",
        worktree_summary="isolated worktree passed",
        test_summary="focused tests passed",
    )
    store.save(session)

    loaded = store.load()
    assert loaded is not None
    assert loaded.status == SESSION_APPROVAL_PENDING
    assert loaded.worktree_summary == "isolated worktree passed"
    assert loaded.test_summary == "focused tests passed"
    assert loaded.apply_allowed is False


def test_only_approved_session_enables_apply(tmp_path: Path) -> None:
    store = EvidencePatchSessionStore(tmp_path / "session.json")
    session = _ready_session(store)
    session = session.transition("VALIDATION_PENDING")
    session = session.transition(SESSION_APPROVAL_PENDING)
    assert session.apply_allowed is False
    session = session.transition(SESSION_APPROVED)
    assert session.apply_allowed is True
