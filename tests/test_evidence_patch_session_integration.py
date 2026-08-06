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
        proposal_id="PP-ABC123",
        status=PROPOSAL_READY_FOR_REVIEW,
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
        objective="Prepare the smallest safe change.",
        rationale="Evidence confidence is high.",
        change_scope=("Target symbol only.",),
        validation_steps=("Run focused tests.",),
        safety_constraints=("Do not bypass worktree.",),
        user_approval_required=True,
        apply_allowed=False,
    )


def test_assistant_creates_persistent_handoff_ready_session(tmp_path: Path) -> None:
    target = tmp_path / "core" / "task_orchestrator.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n", encoding="utf-8")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    calls: list[str] = []
    engine.prepare_own_code_proposal = lambda *args, **kwargs: calls.append(
        "EDIT PROPOSAL READY"
    )

    rendered = engine.prepare_evidence_patch_proposal(_proposal())
    store = EvidencePatchSessionStore(
        tmp_path / ".jarvis" / "evidence_patch_session.json"
    )
    session = store.load()

    assert calls == []
    assert session is not None
    assert session.status == SESSION_HANDOFF_READY
    assert session.apply_allowed is False
    assert "KANIT PATCH OTURUMU" in rendered
    assert "HANDOFF_READY" in rendered
    assert "Edit modeli baslatilmadi" in rendered
