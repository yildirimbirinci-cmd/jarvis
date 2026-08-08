from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.own_code_pending_proposal_store import OwnCodePendingProposalStore
from artmach_assistant.core.own_code_validation_state import OwnCodeValidationStateStore


def _proposal(old: str = "before\n", new: str = "after\n") -> EditProposal:
    return EditProposal(
        "restart safe",
        [ProposedFileChange("core/sample.py", "test", old, new, True)],
    )


def test_pending_proposal_round_trip_requires_unchanged_source(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    target = root / "core" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    store = OwnCodePendingProposalStore(root / ".jarvis" / "pending.json")
    store.save(_proposal(), "fingerprint-1")
    restored = store.load(root)
    assert restored is not None
    assert restored.fingerprint == "fingerprint-1"
    assert restored.proposal.files[0].new_content == "after\n"


def test_pending_proposal_restore_rejects_source_change(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    target = root / "core" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    store = OwnCodePendingProposalStore(root / ".jarvis" / "pending.json")
    store.save(_proposal(), "fingerprint-1")
    target.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        store.load(root)


def test_validation_state_round_trip(tmp_path: Path) -> None:
    store = OwnCodeValidationStateStore(tmp_path / "validation.json")
    store.save(
        phase="validating",
        proposal_fingerprint="fp-123",
        baseline_failures=["tests/test_x.py::test_a"],
        changed_paths=["core/sample.py"],
    )
    state = store.load()
    assert state is not None
    assert state.phase == "validating"
    assert state.proposal_fingerprint == "fp-123"
    assert state.changed_paths == ("core/sample.py",)


def test_validation_state_clear(tmp_path: Path) -> None:
    store = OwnCodeValidationStateStore(tmp_path / "validation.json")
    store.save(phase="validating", proposal_fingerprint="fp")
    store.clear()
    assert store.load() is None


def test_assistant_has_restart_safe_hooks() -> None:
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(encoding="utf-8")
    assert "def _restore_restart_safe_pending_proposal" in source
    assert "def _revalidate_restored_pending_proposal" in source
    assert "own_code_pending_proposal.json" in source
    assert "own_code_validation_state.json" in source
    assert 'stage in {"proposal_ready", "interrupted_validation"}' in source
