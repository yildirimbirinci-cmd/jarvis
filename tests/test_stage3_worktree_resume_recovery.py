from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.own_code_validation_state import OwnCodeValidationStateStore
from artmach_assistant.core.own_code_worktree_recovery import OwnCodeWorktreeRecovery


def test_owned_temporary_worktree_detection(tmp_path: Path) -> None:
    root = tmp_path / "artmach_assistant"
    root.mkdir()
    owned = tmp_path / "jarvis-own-code-worktree-abc" / "artmach_assistant"
    foreign = tmp_path / "other-worktree" / "artmach_assistant"
    assert OwnCodeWorktreeRecovery._is_owned_temporary_worktree(root, owned)
    assert not OwnCodeWorktreeRecovery._is_owned_temporary_worktree(root, foreign)
    assert not OwnCodeWorktreeRecovery._is_owned_temporary_worktree(root, root)


def test_validation_state_can_represent_interrupted_revalidation(tmp_path: Path) -> None:
    store = OwnCodeValidationStateStore(tmp_path / "state.json")
    store.save(
        phase="revalidating",
        proposal_fingerprint="fp-1",
        baseline_failures=["test_a"],
        changed_paths=["core/a.py"],
    )
    state = store.load()
    assert state is not None
    assert state.phase == "revalidating"
    assert state.proposal_fingerprint == "fp-1"


def test_assistant_cross_checks_validation_state_before_resume() -> None:
    source = (Path(__file__).resolve().parents[1] / "core" / "assistant.py").read_text(encoding="utf-8")
    assert "def _validate_restart_safe_resume_state" in source
    assert "OwnCodeWorktreeRecovery" in source
    assert "validation state fingerprint does not match pending proposal" in source
    assert "cleanup_orphans()" in source
