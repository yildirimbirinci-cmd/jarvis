from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.own_code_worktree import OwnCodeWorktreeValidator
from artmach_assistant.core.workspace import WorkspaceError


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "jarvis@example.invalid")
    _git(root, "config", "user.name", "Jarvis Test")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "baseline")
    return root


def _proposal() -> EditProposal:
    return EditProposal(
        "change value",
        [ProposedFileChange("app.py", "test", "VALUE = 1\n", "VALUE = 2\n", True)],
    )


def test_successful_validation_changes_only_temporary_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    seen = []

    result = OwnCodeWorktreeValidator(root).validate(
        _proposal(),
        lambda worktree: (
            seen.append((worktree, (worktree / "app.py").read_text(encoding="utf-8"))) is None,
            "2 passed",
        ),
    )

    assert result.ok
    assert seen[0][1] == "VALUE = 2\n"
    assert (root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not seen[0][0].exists()


def test_failed_validation_never_changes_live_source(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = OwnCodeWorktreeValidator(root).validate(
        _proposal(), lambda _worktree: (False, "test failed")
    )

    assert not result.ok
    assert result.output == "test failed"
    assert (root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_dirty_tracked_source_is_rejected_before_worktree_creation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "app.py").write_text("DIRTY = True\n", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="kaydedilmemiş değişiklikler"):
        OwnCodeWorktreeValidator(root).validate(_proposal(), lambda _root: (True, ""))


def test_stale_proposal_is_rejected_inside_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    stale = EditProposal(
        "stale",
        [ProposedFileChange("app.py", "test", "VALUE = 0\n", "VALUE = 2\n", True)],
    )

    with pytest.raises(WorkspaceError, match="kaynak sürümüyle eşleşmiyor"):
        OwnCodeWorktreeValidator(root).validate(stale, lambda _root: (True, ""))

    assert (root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"

def test_line_ending_difference_does_not_make_proposal_stale(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    proposal = EditProposal(
        "change value",
        [
            ProposedFileChange(
                "app.py",
                "test",
                "VALUE = 1\r\n",
                "VALUE = 2\r\n",
                True,
            )
        ],
    )

    result = OwnCodeWorktreeValidator(root).validate(
        proposal,
        lambda worktree: (
            (worktree / "app.py").read_text(encoding="utf-8")
            == "VALUE = 2\n",
            "line endings accepted",
        ),
    )

    assert result.ok
    assert result.output == "line endings accepted"
    assert (root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
