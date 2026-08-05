from __future__ import annotations

import importlib

from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPLIED,
    SESSION_APPLYING,
    SESSION_APPROVED,
    SESSION_APPROVAL_PENDING,
    SESSION_EDIT_PROPOSAL_READY,
    SESSION_HANDOFF_READY,
    SESSION_VALIDATION_PENDING,
    EvidencePatchSession,
)


def _applied_session() -> EvidencePatchSession:
    session = EvidencePatchSession.create(
        proposal_id="PP-INTEGRATION",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )
    for status in (
        SESSION_HANDOFF_READY,
        SESSION_EDIT_PROPOSAL_READY,
        SESSION_VALIDATION_PENDING,
        SESSION_APPROVAL_PENDING,
        SESSION_APPROVED,
        SESSION_APPLYING,
        SESSION_APPLIED,
    ):
        session = session.transition(status)
    return session


class _History:
    def __init__(self) -> None:
        self.rows = []

    def record(self, event: str, **details) -> None:
        self.rows.append((event, details))


class _Learning:
    def __init__(self) -> None:
        self.rows = []

    def audit(self, event: str, **details: str) -> None:
        self.rows.append((event, details))


def test_engine_records_successful_closed_session() -> None:
    module = importlib.import_module("artmach_assistant.core.assistant")
    engine = module.AssistantEngine.__new__(module.AssistantEngine)
    engine.own_code_history = _History()
    engine.learning_memory = _Learning()
    session = _applied_session().with_closeout(
        retest_summary="passed",
        closeout_summary="completed",
        completed=True,
    )
    recorded = engine._record_evidence_patch_outcome(
        session,
        successful=True,
        note="completed",
    )
    assert recorded.closed_at
    assert "gecmisine yazildi" in recorded.journal_summary
    assert "audit kaydina yazildi" in recorded.memory_summary
    assert recorded.apply_allowed is False
