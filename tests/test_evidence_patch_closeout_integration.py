from __future__ import annotations

import importlib
from pathlib import Path

from artmach_assistant.core.evidence_patch_closeout import (
    CLOSEOUT_COMPLETED,
    EvidencePatchCloseoutResult,
)
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
from artmach_assistant.core.evidence_retest import RetestPlan


def _assistant_module():
    return importlib.import_module(
        "artmach_assistant.core.assistant"
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


def test_engine_records_completed_post_apply_closeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    assistant_module = _assistant_module()
    AssistantEngine = assistant_module.AssistantEngine
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine._build_evidence_retest_plan = lambda: RetestPlan(())

    outcome = EvidencePatchCloseoutResult(
        status=CLOSEOUT_COMPLETED,
        session_id="PS-EXAMPLE",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
        reason="Primary retest passed.",
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
    assert "Primary retest passed" in closed.closeout_summary
    assert closed.apply_allowed is False
