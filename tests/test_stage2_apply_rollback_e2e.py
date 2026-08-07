from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPLIED,
    SESSION_APPROVAL_PENDING,
    SESSION_APPROVED,
    SESSION_EDIT_PROPOSAL_READY,
    SESSION_FAILED,
    SESSION_HANDOFF_READY,
    SESSION_VALIDATION_PENDING,
    EvidencePatchSession,
    EvidencePatchSessionStore,
)


def _session(tmp_path: Path):
    store = EvidencePatchSessionStore(
        tmp_path / ".jarvis" / "evidence_patch_session.json"
    )
    session = EvidencePatchSession.create(
        proposal_id="PP-E2E",
        target_path="core/example.py",
        target_symbol="example.VALUE",
    )
    for status in (
        SESSION_HANDOFF_READY,
        SESSION_EDIT_PROPOSAL_READY,
        SESSION_VALIDATION_PENDING,
        SESSION_APPROVAL_PENDING,
        SESSION_APPROVED,
    ):
        session = session.transition(status)
    store.save(session)
    return store, session


def _engine(tmp_path: Path, *, apply_result: str):
    from artmach_assistant.core.assistant import AssistantEngine

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    proposal = EditProposal(
        summary="e2e",
        files=[
            ProposedFileChange(
                path="core/example.py",
                reason="e2e",
                old_content="VALUE = 1\n",
                new_content="VALUE = 2\n",
                existed=True,
            )
        ],
    )
    engine.editor = SimpleNamespace(pending=proposal)
    engine.apply_pending_own_code_proposal = lambda: apply_result
    engine._record_evidence_patch_outcome = lambda session, **kwargs: session
    engine._closeout_applied_evidence_patch_session = lambda session: session
    engine._finalize_safe_release = lambda session, changed_paths: "release-ok"
    return engine


def test_successful_apply_transitions_to_applied(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    store, session = _session(tmp_path)
    engine = _engine(
        tmp_path,
        apply_result="Onayladigin kod degisikligi uygulandi.",
    )
    engine.editor.pending = None

    rendered = engine.apply_evidence_patch_session(session.session_id)
    stored = store.load()

    assert stored is not None
    assert stored.status == SESSION_APPLIED
    assert stored.apply_allowed is False
    assert "APPLIED" in rendered
    assert "release-ok" in rendered


def test_rollback_result_transitions_to_failed_with_verified_rollback(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    store, session = _session(tmp_path)
    engine = _engine(
        tmp_path,
        apply_result="Dogrulama basarisiz; degisiklik geri alindi.",
    )

    captured = {}

    def record(session, **kwargs):
        captured.update(kwargs)
        return session

    engine._record_evidence_patch_outcome = record

    rendered = engine.apply_evidence_patch_session(session.session_id)
    stored = store.load()

    assert stored is not None
    assert stored.status == SESSION_FAILED
    assert stored.apply_allowed is False
    assert "geri aldi" in stored.rollback_summary
    assert captured["successful"] is False
    assert captured["rollback_verified"] is True
    assert "FAILED" in rendered


def test_unexpected_apply_exception_transitions_to_failed(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    store, session = _session(tmp_path)
    engine = _engine(tmp_path, apply_result="unused")

    def explode():
        raise RuntimeError("synthetic failure")

    engine.apply_pending_own_code_proposal = explode

    rendered = engine.apply_evidence_patch_session(session.session_id)
    stored = store.load()

    assert stored is not None
    assert stored.status == SESSION_FAILED
    assert stored.apply_allowed is False
    assert "synthetic failure" in stored.error
    assert "FAILED" in rendered
