from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from artmach_assistant.core import assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.own_code_pending_proposal_store import OwnCodePendingProposalStore
from artmach_assistant.core.own_code_approval import proposal_fingerprint


def _proposal(old: str = "before\n", new: str = "after\n") -> EditProposal:
    return EditProposal("restart safe", [ProposedFileChange("core/sample.py", "test", old, new, True)])


def test_pending_proposal_round_trip_requires_unchanged_source(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    target = root / "core" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    store = OwnCodePendingProposalStore(tmp_path / "pending.json")
    proposal = _proposal()
    fingerprint = proposal_fingerprint(proposal)
    store.save(proposal, fingerprint)
    restored = store.load(root)
    assert restored is not None
    assert restored.fingerprint == fingerprint
    assert restored.proposal.files[0].new_content == "after\n"


def test_pending_proposal_restore_rejects_source_change(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    target = root / "core" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    store = OwnCodePendingProposalStore(tmp_path / "pending.json")
    proposal = _proposal()
    store.save(proposal, proposal_fingerprint(proposal))
    target.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        store.load(root)


def test_pending_proposal_restore_accepts_windows_line_endings(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    target = root / "core" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"before\r\n")
    store = OwnCodePendingProposalStore(tmp_path / "pending.json")
    proposal = _proposal()
    store.save(proposal, proposal_fingerprint(proposal))
    assert store.load(root) is not None


def test_assistant_restore_uses_isolated_store_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    target = root / "core" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    state_path = tmp_path / "state" / "pending.json"
    monkeypatch.setattr(assistant_module, "OWN_CODE_PENDING_PROPOSAL_FILE", state_path)
    proposal = _proposal()
    fingerprint = proposal_fingerprint(proposal)
    OwnCodePendingProposalStore(state_path).save(proposal, fingerprint)
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    engine.own_project_root = lambda: root
    restored, detail = engine._restore_restart_safe_pending_proposal()
    assert restored is True
    assert "verified" in detail
    assert engine.editor.pending is not None
    assert engine._pending_own_code_fingerprint == fingerprint


def test_cycle_report_does_not_restore_pending_as_side_effect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state_path = tmp_path / "pending.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(assistant_module, "OWN_CODE_PENDING_PROPOSAL_FILE", state_path)
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    engine._load_own_code_cycle = lambda: {"version": 4, "stage": "proposal_ready", "attempt": 1, "failures": [], "changed_paths": []}
    report = engine.own_code_cycle_report()
    assert engine.editor.pending is None
    assert "restart-safe" in report.casefold()


def test_legacy_versionless_proposal_ready_keeps_lost_proposal_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        assistant_module,
        "OWN_CODE_PENDING_PROPOSAL_FILE",
        tmp_path / "pending.json",
    )
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    state = {
        "stage": "proposal_ready",
        "detail": "legacy",
        "attempt": 1,
        "failures": [],
    }
    engine._load_own_code_cycle = lambda: state

    def save(stage: str, detail: str, **kwargs) -> None:
        state["stage"] = stage
        state["detail"] = detail
        state.update(kwargs)

    engine._save_own_code_cycle = save
    result = engine.own_code_cycle_report()
    assert "bellekte tutulmamış" in result
    assert "yeni onay kimliğiyle" in result
