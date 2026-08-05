from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPLIED,
    SESSION_APPROVED,
    SESSION_FAILED,
    EvidencePatchSession,
    EvidencePatchSessionStore,
)


def _approved_store(tmp_path: Path) -> tuple[EvidencePatchSessionStore, EvidencePatchSession]:
    store = EvidencePatchSessionStore(
        tmp_path / ".jarvis" / "evidence_patch_session.json"
    )
    session = EvidencePatchSession.create(
        proposal_id="PP-APPLY",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )
    for status in (
        "HANDOFF_READY",
        "EDIT_PROPOSAL_READY",
        "VALIDATION_PENDING",
        "APPROVAL_PENDING",
        SESSION_APPROVED,
    ):
        session = session.transition(status)
    store.save(session)
    return store, session


def _engine(tmp_path: Path, result: str, *, pending_after: object) -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine.editor = SimpleNamespace(pending=object())

    def apply_result() -> str:
        engine.editor.pending = pending_after
        return result

    engine.apply_pending_own_code_proposal = apply_result
    return engine


def test_apply_moves_approved_session_to_applied(tmp_path: Path) -> None:
    store, session = _approved_store(tmp_path)
    engine = _engine(
        tmp_path,
        "Onayladigin kod degisikligi uygulandi. "
        "Geri donus noktasi: C:/checkpoint/42 "
        "Surum kaydi: version-42",
        pending_after=None,
    )

    rendered = engine.apply_evidence_patch_session(session.session_id)
    saved = store.load()

    assert saved is not None
    assert saved.status == SESSION_APPLIED
    assert "version-42" in saved.version_summary
    assert "APPLIED" in rendered


def test_apply_failure_is_persisted_and_not_reported_success(tmp_path: Path) -> None:
    store, session = _approved_store(tmp_path)
    engine = _engine(
        tmp_path,
        "Degisiklik yeni test hatasi nedeniyle geri alindi.",
        pending_after=object(),
    )

    rendered = engine.apply_evidence_patch_session(session.session_id)
    saved = store.load()

    assert saved is not None
    assert saved.status == SESSION_FAILED
    assert "geri aldi" in saved.rollback_summary
    assert "FAILED" in rendered
