from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_patch_proposal import (
    PROPOSAL_BLOCKED,
    PROPOSAL_READY_FOR_REVIEW,
    EvidencePatchProposal,
)


def _proposal(status: str = PROPOSAL_READY_FOR_REVIEW) -> EvidencePatchProposal:
    return EvidencePatchProposal(
        proposal_id="PP-ABC123",
        status=status,
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
        objective="Darbogazi olc ve en kucuk guvenli degisikligi hazirla.",
        rationale="Kanit guveni yuksek.",
        change_scope=("Hedef sembol disina cikma.",),
        validation_steps=("Hedef testleri calistir.",),
        safety_constraints=("Worktree zincirini atlama.",),
        user_approval_required=True,
        apply_allowed=False,
    )


def test_assistant_routes_ready_handoff_to_existing_generator(tmp_path: Path) -> None:
    target = tmp_path / "core" / "task_orchestrator.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n", encoding="utf-8")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    captured = {}

    def prepare(instruction: str, **kwargs) -> str:
        captured["instruction"] = instruction
        captured.update(kwargs)
        return "EDIT PROPOSAL READY"

    engine.prepare_own_code_proposal = prepare
    rendered = engine.prepare_evidence_patch_proposal(_proposal())

    assert "Durum: READY" in rendered
    assert "EDIT PROPOSAL READY" in rendered
    assert captured["production_repair"] is True
    assert captured["approved_paths"] == ("core/task_orchestrator.py",)
    assert captured["approved_symbols"] == ("TaskOrchestrator.wrap.execute",)
    assert captured["plan_id"] == "PP-ABC123"


def test_assistant_does_not_call_generator_for_blocked_handoff(tmp_path: Path) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    engine.prepare_own_code_proposal = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("generator must not be called")
    )

    rendered = engine.prepare_evidence_patch_proposal(
        _proposal(PROPOSAL_BLOCKED)
    )

    assert "Durum: BLOCKED" in rendered
    assert "Uygulama izni: hayir" in rendered
