from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPROVAL_PENDING,
    SESSION_APPROVED,
    SESSION_EDIT_PROPOSAL_READY,
    SESSION_HANDOFF_READY,
    SESSION_VALIDATION_PENDING,
    EvidencePatchSession,
    EvidencePatchSessionStore,
)
from artmach_assistant.core.evidence_research_command import (
    EvidenceResearchCommandCoordinator,
)
from artmach_assistant.core.evidence_research_executor import (
    EvidenceResearchExecutionResult,
    RESEARCH_COMPLETED,
)
from artmach_assistant.core.evidence_research_session import (
    PENDING,
    EvidenceResearchApprovalSession,
    EvidenceResearchApprovalStore,
)


def _assistant_module():
    return importlib.import_module(
        "artmach_assistant.core.assistant"
    )


def _research_session() -> EvidenceResearchApprovalSession:
    return EvidenceResearchApprovalSession(
        schema_version=1,
        approval_id="RS-ABCDEF1234",
        status=PENDING,
        title="Example.run failure",
        path="core/example.py",
        symbol="Example.run",
        reason="Local evidence was insufficient.",
        local_questions=("Inspect local code.",),
        external_queries=("Python profiling documentation",),
        preferred_sources=("Official documentation",),
        safety_constraints=("Do not apply web code directly.",),
        created_at="2026-08-05T08:00:00+00:00",
        updated_at="2026-08-05T08:00:00+00:00",
    )


def _research_result() -> EvidenceResearchExecutionResult:
    return EvidenceResearchExecutionResult(
        status=RESEARCH_COMPLETED,
        approval_id="RS-ABCDEF1234",
        title="Example.run failure",
        path="core/example.py",
        symbol="Example.run",
        queries=("Python profiling documentation",),
        reason="Research completed.",
    )


def test_rs_approval_appends_engineering_follow_up(
    tmp_path: Path,
) -> None:
    store = EvidenceResearchApprovalStore(
        tmp_path / "pending_research.json"
    )
    store.save(_research_session())
    handled: list[EvidenceResearchExecutionResult] = []

    def result_handler(result):
        handled.append(result)
        return "PATCH SESSION: APPROVAL_PENDING"

    coordinator = EvidenceResearchCommandCoordinator(
        store=store,
        executor=lambda _session: _research_result(),
        result_handler=result_handler,
    )

    rendered = coordinator.handle("RS-ABCDEF1234 onayla")

    assert rendered is not None
    assert "DIS ARASTIRMA SONUCU" in rendered
    assert "PATCH SESSION: APPROVAL_PENDING" in rendered
    assert len(handled) == 1


def test_research_result_prepares_and_validates_patch_session() -> None:
    AssistantEngine = _assistant_module().AssistantEngine
    engine = AssistantEngine.__new__(AssistantEngine)
    session = SimpleNamespace(status=SESSION_EDIT_PROPOSAL_READY)
    store = SimpleNamespace(load=lambda: session)
    engine.prepare_evidence_patch_proposal = (
        lambda proposal: "PREPARED"
    )
    engine._evidence_patch_session_store = lambda: store
    engine.validate_evidence_patch_session = lambda: "VALIDATED"

    rendered = engine._handle_evidence_research_result(
        SimpleNamespace(patch_proposal=object())
    )

    assert rendered == "PREPARED\n\nVALIDATED"


def _approval_pending_session() -> EvidencePatchSession:
    session = EvidencePatchSession.create(
        proposal_id="PP-ORCHESTRATION",
        target_path="core/example.py",
        target_symbol="Example.run",
    )
    for status in (
        SESSION_HANDOFF_READY,
        SESSION_EDIT_PROPOSAL_READY,
        SESSION_VALIDATION_PENDING,
        SESSION_APPROVAL_PENDING,
    ):
        session = session.transition(status)
    return session


def test_exact_ps_approval_runs_approve_and_apply(
    tmp_path: Path,
) -> None:
    AssistantEngine = _assistant_module().AssistantEngine
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    store = EvidencePatchSessionStore(
        tmp_path / ".jarvis" / "evidence_patch_session.json"
    )
    session = _approval_pending_session()
    store.save(session)
    calls: list[str] = []

    def approve(session_id: str) -> str:
        calls.append("approve:" + session_id)
        current = store.load()
        assert current is not None
        store.save(current.transition(SESSION_APPROVED))
        return "APPROVED"

    def apply(session_id: str) -> str:
        calls.append("apply:" + session_id)
        return "APPLIED AND CLOSED"

    engine.approve_evidence_patch_session = approve
    engine.apply_evidence_patch_session = apply

    rendered = engine._patch_session_command_request(
        f"{session.session_id} onayla"
    )

    assert rendered == "APPROVED\n\nAPPLIED AND CLOSED"
    assert calls == [
        "approve:" + session.session_id,
        "apply:" + session.session_id,
    ]


def test_wrong_ps_id_never_calls_apply(tmp_path: Path) -> None:
    AssistantEngine = _assistant_module().AssistantEngine
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    store = EvidencePatchSessionStore(
        tmp_path / ".jarvis" / "evidence_patch_session.json"
    )
    store.save(_approval_pending_session())
    engine.approve_evidence_patch_session = lambda _id: "BAD"
    engine.apply_evidence_patch_session = lambda _id: (_ for _ in ()).throw(
        AssertionError("apply must not run")
    )

    rendered = engine._patch_session_command_request(
        "PS-000000000000 onayla"
    )

    assert rendered is not None
    assert "eslesmiyor" in rendered
    assert "degistirilmedi" in rendered
