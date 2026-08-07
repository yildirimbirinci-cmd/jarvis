from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPROVAL_PENDING,
    SESSION_APPROVED,
    SESSION_FAILED,
    SESSION_HANDOFF_READY,
    SESSION_EDIT_PROPOSAL_READY,
    EvidencePatchSession,
    EvidencePatchSessionStore,
)


def _assistant_module():
    return importlib.import_module("artmach_assistant.core.assistant")


def _engine(tmp_path: Path):
    AssistantEngine = _assistant_module().AssistantEngine
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    proposal = EditProposal(
        summary="safe proposal",
        files=[
            ProposedFileChange(
                path="core/example.py",
                reason="apply failure state guard",
                old_content="VALUE = 1\n",
                new_content="VALUE = 2\n",
                existed=True,
            )
        ],
    )
    engine.editor = SimpleNamespace(pending=proposal)
    engine._record_evidence_patch_outcome = lambda session, **kwargs: session
    return engine


def _approved_session(engine, tmp_path: Path) -> EvidencePatchSession:
    store = EvidencePatchSessionStore(
        tmp_path / ".jarvis" / "evidence_patch_session.json"
    )
    session = EvidencePatchSession.create(
        proposal_id="PP-APPLY-GUARD",
        target_path="core/example.py",
        target_symbol="example.VALUE",
    )
    session = session.transition(SESSION_HANDOFF_READY)
    session = session.transition(SESSION_EDIT_PROPOSAL_READY)
    session = session.transition("VALIDATION_PENDING")
    session = session.transition(SESSION_APPROVAL_PENDING)
    session = session.transition(SESSION_APPROVED)
    store.save(session)
    return session


def test_unexpected_apply_exception_does_not_leave_session_applying(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    session = _approved_session(engine, tmp_path)

    def explode():
        raise RuntimeError("synthetic apply failure")

    engine.apply_pending_own_code_proposal = explode

    rendered = engine.apply_evidence_patch_session(session.session_id)
    stored = engine._evidence_patch_session_store().load()

    assert stored is not None
    assert stored.status == SESSION_FAILED
    assert stored.apply_allowed is False
    assert "RuntimeError: synthetic apply failure" in stored.error
    assert "FAILED" in rendered
    assert "beklenmedik bicimde kesildi" in rendered
