from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_patch_proposal import (
    PROPOSAL_BLOCKED,
    PROPOSAL_READY_FOR_REVIEW,
    EvidencePatchProposal,
)
from artmach_assistant.core.evidence_patch_session import (
    SESSION_HANDOFF_READY,
    EvidencePatchSessionStore,
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


def test_assistant_stages_ready_handoff_without_generator(tmp_path: Path) -> None:
    target = tmp_path / "core" / "task_orchestrator.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n", encoding="utf-8")
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def prepare(*args, **kwargs) -> str:
        calls.append((args, kwargs))
        return "EDIT PROPOSAL READY"

    engine.prepare_own_code_proposal = prepare
    rendered = engine.prepare_evidence_patch_proposal(_proposal())

    assert calls == []
    assert "Durum: HANDOFF_READY" in rendered
    assert "Edit modeli baslatilmadi" in rendered
    assert "Uygulama izni: hayir" in rendered

    store = EvidencePatchSessionStore(
        tmp_path / ".jarvis" / "evidence_patch_session.json"
    )
    session = store.load()
    assert session is not None
    assert session.status == SESSION_HANDOFF_READY
    assert session.apply_allowed is False


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
