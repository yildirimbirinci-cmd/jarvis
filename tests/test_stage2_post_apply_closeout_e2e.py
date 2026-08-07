from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.evidence_patch_closeout import (
    CLOSEOUT_COMPLETED,
    CLOSEOUT_PENDING,
    EvidencePatchCloseoutResult,
)
from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPLIED,
    SESSION_APPROVAL_PENDING,
    SESSION_APPROVED,
    SESSION_EDIT_PROPOSAL_READY,
    SESSION_HANDOFF_READY,
    SESSION_VALIDATION_PENDING,
    EvidencePatchSession,
)


def _applied_session() -> EvidencePatchSession:
    session = EvidencePatchSession.create(
        proposal_id="PP-CLOSEOUT-E2E",
        target_path="core/example.py",
        target_symbol="example.VALUE",
    )
    for status in (
        SESSION_HANDOFF_READY,
        SESSION_EDIT_PROPOSAL_READY,
        SESSION_VALIDATION_PENDING,
        SESSION_APPROVAL_PENDING,
        SESSION_APPROVED,
        "APPLYING",
        SESSION_APPLIED,
    ):
        session = session.transition(status)
    return session


def test_successful_post_apply_closeout_sets_closed_at(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from artmach_assistant.core import assistant as assistant_module

    engine = assistant_module.AssistantEngine.__new__(
        assistant_module.AssistantEngine
    )
    engine.own_project_root = lambda: tmp_path
    engine._build_evidence_retest_plan = lambda: SimpleNamespace(items=())

    outcome = EvidencePatchCloseoutResult(
        status=CLOSEOUT_COMPLETED,
        session_id="PS-TEST",
        target_path="core/example.py",
        target_symbol="example.VALUE",
        reason="Primary yeniden test gecti.",
    )
    monkeypatch.setattr(
        assistant_module,
        "run_patch_closeout",
        lambda *args, **kwargs: outcome,
    )

    closed = engine._closeout_applied_evidence_patch_session(
        _applied_session()
    )

    assert closed.status == SESSION_APPLIED
    assert closed.closed_at
    assert closed.error == ""
    assert "PATCH SONRASI KAPATMA" in closed.closeout_summary
    assert "COMPLETED" in closed.closeout_summary


def test_incomplete_post_apply_closeout_does_not_mark_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from artmach_assistant.core import assistant as assistant_module

    engine = assistant_module.AssistantEngine.__new__(
        assistant_module.AssistantEngine
    )
    engine.own_project_root = lambda: tmp_path
    engine._build_evidence_retest_plan = lambda: SimpleNamespace(items=())

    outcome = EvidencePatchCloseoutResult(
        status=CLOSEOUT_PENDING,
        session_id="PS-TEST",
        target_path="core/example.py",
        target_symbol="example.VALUE",
        reason="Kesin eslesen yeniden test maddesi bulunamadi.",
    )
    monkeypatch.setattr(
        assistant_module,
        "run_patch_closeout",
        lambda *args, **kwargs: outcome,
    )

    closed = engine._closeout_applied_evidence_patch_session(
        _applied_session()
    )

    assert closed.status == SESSION_APPLIED
    assert closed.closed_at == ""
    assert closed.error
    assert "PENDING" in closed.closeout_summary


def test_successful_patch_outcome_records_history_and_learning() -> None:
    from artmach_assistant.core.assistant import AssistantEngine

    history_rows = []
    learning_rows = []

    class History:
        def record(self, event: str, **details):
            history_rows.append((event, details))

    class Learning:
        def audit(self, event: str, **details):
            learning_rows.append((event, details))

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_code_history = History()
    engine.learning_memory = Learning()

    session = _applied_session().with_closeout(
        retest_summary="PASSED",
        closeout_summary="COMPLETED",
        completed=True,
    )
    recorded = engine._record_evidence_patch_outcome(
        session,
        successful=True,
        note="post-apply closeout passed",
    )

    assert history_rows
    assert learning_rows
    assert history_rows[0][0] == "evidence_patch_outcome"
    assert learning_rows[0][0] == "evidence_patch_outcome"
    assert recorded.journal_summary
    assert recorded.memory_summary
    assert recorded.error == ""


def test_safe_release_is_blocked_until_closeout_complete(tmp_path: Path) -> None:
    from artmach_assistant.core.assistant import AssistantEngine

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path

    result = engine._finalize_safe_release(
        _applied_session(),
        ("core/example.py",),
    )

    assert "closeout is incomplete" in result
