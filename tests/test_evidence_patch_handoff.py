from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.evidence_patch_handoff import (
    HANDOFF_BLOCKED,
    HANDOFF_READY,
    build_evidence_patch_handoff,
)
from artmach_assistant.core.evidence_patch_proposal import (
    PROPOSAL_BLOCKED,
    PROPOSAL_READY_FOR_REVIEW,
    EvidencePatchProposal,
)


def _proposal(**overrides) -> EvidencePatchProposal:
    values = {
        "proposal_id": "PP-ABC123",
        "status": PROPOSAL_READY_FOR_REVIEW,
        "target_path": "core/task_orchestrator.py",
        "target_symbol": "TaskOrchestrator.wrap.execute",
        "objective": "Darbogazi kanita dayali olarak hedefle.",
        "rationale": "Kanit guveni yuksek.",
        "change_scope": ("Yalnizca hedef sembolde en kucuk degisiklik.",),
        "validation_steps": ("Hedef testleri calistir.", "Tam regresyonu calistir."),
        "safety_constraints": ("Worktree zincirini atlama.",),
        "user_approval_required": True,
        "apply_allowed": False,
    }
    values.update(overrides)
    return EvidencePatchProposal(**values)


def test_ready_proposal_builds_scoped_handoff(tmp_path: Path) -> None:
    target = tmp_path / "core" / "task_orchestrator.py"
    target.parent.mkdir(parents=True)
    target.write_text("class TaskOrchestrator:\n    pass\n", encoding="utf-8")

    handoff = build_evidence_patch_handoff(
        _proposal(),
        project_root=tmp_path,
    )

    assert handoff.status == HANDOFF_READY
    assert handoff.apply_allowed is False
    assert handoff.approved_paths == ("core/task_orchestrator.py",)
    assert handoff.approved_symbols == ("TaskOrchestrator.wrap.execute",)
    assert "Yalnizca incelemeye hazir bir EditProposal uret" in handoff.instruction


def test_blocked_proposal_cannot_enter_edit_pipeline(tmp_path: Path) -> None:
    handoff = build_evidence_patch_handoff(
        _proposal(status=PROPOSAL_BLOCKED),
        project_root=tmp_path,
    )

    assert handoff.status == HANDOFF_BLOCKED
    assert handoff.instruction == ""
    assert handoff.approved_paths == ()


def test_path_escape_is_blocked(tmp_path: Path) -> None:
    handoff = build_evidence_patch_handoff(
        _proposal(target_path="../outside.py"),
        project_root=tmp_path,
    )

    assert handoff.status == HANDOFF_BLOCKED
    assert "disina" in handoff.reason


def test_apply_permission_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "core" / "task_orchestrator.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n", encoding="utf-8")

    handoff = build_evidence_patch_handoff(
        _proposal(apply_allowed=True),
        project_root=tmp_path,
    )

    assert handoff.status == HANDOFF_BLOCKED
    assert "uygulama izni" in handoff.reason
