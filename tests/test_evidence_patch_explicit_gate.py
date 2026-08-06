from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_patch_proposal import (
    PROPOSAL_READY_FOR_REVIEW,
    EvidencePatchProposal,
)
from artmach_assistant.core.evidence_patch_session import (
    SESSION_HANDOFF_READY,
    EvidencePatchSessionStore,
)


def _proposal() -> EvidencePatchProposal:
    return EvidencePatchProposal(
        proposal_id="PP-EXPLICIT1",
        status=PROPOSAL_READY_FOR_REVIEW,
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
        objective="Prepare a narrow review-only edit proposal.",
        rationale="Evidence is ready for review.",
        change_scope=("Measure wrapper overhead.",),
        validation_steps=("Run target tests.",),
        safety_constraints=("Do not apply source changes.",),
        user_approval_required=True,
        apply_allowed=False,
    )


def test_research_result_stages_without_invoking_code_model(tmp_path: Path) -> None:
    target = tmp_path / "core" / "task_orchestrator.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "class TaskOrchestrator:\n"
        "    def wrap(self):\n"
        "        def execute():\n"
        "            return None\n"
        "        return execute\n",
        encoding="utf-8",
    )

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    calls: list[str] = []
    engine.prepare_own_code_proposal = lambda *_args, **_kwargs: calls.append("called")

    rendered = engine.prepare_evidence_patch_proposal(_proposal())

    assert calls == []
    assert "Durum: HANDOFF_READY" in rendered
    assert "Edit modeli baslatilmadi" in rendered
    store = EvidencePatchSessionStore(tmp_path / ".jarvis" / "evidence_patch_session.json")
    stored = store.load()
    assert stored is not None
    assert stored.status == SESSION_HANDOFF_READY


def test_explicit_ps_approval_starts_edit_generation_only(tmp_path: Path) -> None:
    target = tmp_path / "core" / "task_orchestrator.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "class TaskOrchestrator:\n"
        "    def wrap(self):\n"
        "        def execute():\n"
        "            return None\n"
        "        return execute\n",
        encoding="utf-8",
    )

    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    staged = engine.prepare_evidence_patch_proposal(_proposal())
    store = EvidencePatchSessionStore(tmp_path / ".jarvis" / "evidence_patch_session.json")
    session = store.load()
    assert session is not None
    calls: list[str] = []
    engine._generate_staged_evidence_patch_proposal = lambda value: calls.append(value.session_id) or "EDIT STARTED"

    rendered = engine._patch_session_command_request(f"{session.session_id} onayla")

    assert rendered == "EDIT STARTED"
    assert calls == [session.session_id]
    assert "Edit modeli baslatilmadi" in staged
