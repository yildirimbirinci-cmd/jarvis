from __future__ import annotations

import inspect
from types import MethodType

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_patch_session import (
    EvidencePatchSession,
    SESSION_APPROVAL_PENDING,
    SESSION_EDIT_PROPOSAL_READY,
    SESSION_HANDOFF_READY,
    SESSION_VALIDATION_PENDING,
)


class _Store:
    def __init__(self, session):
        self.session = session
        self.saved = []

    def load(self):
        return self.session

    def save(self, session):
        self.session = session
        self.saved.append(session)


class _Editor:
    pending = None

    def __init__(self):
        self.rejected = False

    def reject(self):
        self.rejected = True


def _engine_with_store(session):
    engine = AssistantEngine.__new__(AssistantEngine)
    store = _Store(session)
    engine._evidence_patch_session_store = MethodType(
        lambda self: store,
        engine,
    )
    engine.editor = _Editor()
    calls = []

    def _record(self, current, *, successful, note, rollback_verified=None):
        calls.append(
            {
                "status": current.status,
                "successful": successful,
                "note": note,
                "rollback_verified": rollback_verified,
            }
        )
        return current

    engine._record_evidence_patch_outcome = MethodType(_record, engine)
    return engine, store, calls


def _session_at_edit_proposal_ready():
    return (
        EvidencePatchSession.create(
            proposal_id="PP-STAGE7",
            target_path="core/example.py",
            target_symbol="Example.run",
        )
        .transition(SESSION_HANDOFF_READY)
        .transition(SESSION_EDIT_PROPOSAL_READY)
    )


def _session_at_approval_pending():
    return (
        _session_at_edit_proposal_ready()
        .transition(SESSION_VALIDATION_PENDING)
        .transition(SESSION_APPROVAL_PENDING)
    )


def test_validation_missing_edit_proposal_records_failed_outcome() -> None:
    session = _session_at_edit_proposal_ready()
    engine, store, calls = _engine_with_store(session)

    rendered = engine.validate_evidence_patch_session()

    assert calls == [
        {
            "status": "FAILED",
            "successful": False,
            "note": "Bekleyen gecerli EditProposal bulunamadi.",
            "rollback_verified": None,
        }
    ]
    assert store.session.status == "FAILED"
    assert "Bekleyen gecerli EditProposal bulunamadi." in rendered


def test_user_rejection_records_terminal_outcome_once() -> None:
    session = _session_at_approval_pending()
    engine, store, calls = _engine_with_store(session)

    rendered = engine.reject_evidence_patch_session(session.session_id)

    assert calls == [
        {
            "status": "REJECTED",
            "successful": False,
            "note": "Kullanici patch oturumunu reddetti.",
            "rollback_verified": None,
        }
    ]
    assert store.session.status == "REJECTED"
    assert engine.editor.rejected is True
    assert "REJECTED" in rendered


def test_terminal_evidence_routes_are_wired_to_outcome_persistence() -> None:
    prepare = inspect.getsource(AssistantEngine.prepare_evidence_patch_proposal)
    generate = inspect.getsource(
        AssistantEngine._generate_staged_evidence_patch_proposal
    )
    validate = inspect.getsource(AssistantEngine.validate_evidence_patch_session)
    reject = inspect.getsource(AssistantEngine.reject_evidence_patch_session)

    assert "_record_evidence_patch_outcome(" in prepare
    assert "_record_evidence_patch_outcome(" in generate
    assert validate.count("_record_evidence_patch_outcome(") >= 3
    assert "_record_evidence_patch_outcome(" in reject
