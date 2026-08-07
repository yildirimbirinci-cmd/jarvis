from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_patch_proposal import (
    EvidencePatchProposal,
    PROPOSAL_READY_FOR_REVIEW,
)
from artmach_assistant.core.evidence_patch_proposal_store import EvidencePatchProposalStore
from artmach_assistant.core.evidence_patch_session import EvidencePatchSession


def _proposal() -> EvidencePatchProposal:
    return EvidencePatchProposal(
        proposal_id="PP-ABCDEF1234",
        status=PROPOSAL_READY_FOR_REVIEW,
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
        objective="Prepare a narrow behavior-preserving patch draft.",
        rationale="Evidence is strong but local validation is still required.",
        change_scope=("Compare action and wrapper duration.",),
        validation_steps=("Run focused and full regression tests.",),
        safety_constraints=("Do not bypass validator or worktree checks.",),
        user_approval_required=True,
        apply_allowed=False,
    )


def test_proposal_store_round_trip(tmp_path: Path) -> None:
    store = EvidencePatchProposalStore(tmp_path / "proposal.json")
    proposal = _proposal()
    store.save(proposal)
    assert store.load() == proposal


def test_restart_loader_restores_matching_session_proposal(tmp_path: Path) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    proposal = _proposal()
    engine._evidence_patch_proposal_store().save(proposal)
    session = EvidencePatchSession.create(
        proposal_id=proposal.proposal_id,
        target_path=proposal.target_path,
        target_symbol=proposal.target_symbol,
    )
    restored = engine._load_staged_evidence_patch_proposal(session)
    assert restored == proposal
    assert engine._pending_evidence_patch_proposal == proposal


def test_restart_loader_rejects_identity_mismatch(tmp_path: Path) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    proposal = _proposal()
    engine._evidence_patch_proposal_store().save(proposal)
    session = EvidencePatchSession.create(
        proposal_id="PP-0000000000",
        target_path=proposal.target_path,
        target_symbol=proposal.target_symbol,
    )
    assert engine._load_staged_evidence_patch_proposal(session) is None
    assert not hasattr(engine, "_pending_evidence_patch_proposal")


def test_proposal_store_rejects_unsafe_permissions(tmp_path: Path) -> None:
    path = tmp_path / "proposal.json"
    payload = EvidencePatchProposalStore._payload(_proposal())
    payload["apply_allowed"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = EvidencePatchProposalStore(path)
    try:
        store.load()
    except ValueError as exc:
        assert "Unsafe" in str(exc)
    else:
        raise AssertionError("Unsafe persisted proposal was accepted.")
