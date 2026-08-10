from __future__ import annotations

import os
import subprocess
from pathlib import Path

from artmach_assistant.core.own_code_worktree import OwnCodeWorktreeValidator


def _completed(args: tuple[str, ...], *, stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(list(args), returncode, stdout=stdout, stderr="")


def test_dead_owned_managed_worktree_is_removed(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    parent = temp_root / "jarvis-own-code-worktree-dead"
    worktree = parent / repo.name
    worktree.mkdir(parents=True)
    (parent / ".jarvis-worktree-owner").write_text("99999999", encoding="ascii")

    validator = OwnCodeWorktreeValidator(repo)
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, cwd=None):
        calls.append(tuple(args))
        if args == ("worktree", "list", "--porcelain"):
            return _completed(args, stdout=f"worktree {worktree}\nHEAD deadbeef\ndetached\n")
        return _completed(args)

    monkeypatch.setattr("artmach_assistant.core.own_code_worktree.tempfile.gettempdir", lambda: str(temp_root))
    monkeypatch.setattr(validator, "_git", fake_git)
    monkeypatch.setattr(validator, "_pid_is_running", lambda pid: False)

    recovered = validator.recover_stale_worktrees()

    assert parent in recovered
    assert not parent.exists()
    assert ("worktree", "remove", "--force", str(worktree.resolve(strict=False))) in calls
    assert ("worktree", "prune") in calls


def test_live_owned_managed_worktree_is_preserved(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    parent = temp_root / "jarvis-own-code-worktree-live"
    worktree = parent / repo.name
    worktree.mkdir(parents=True)
    (parent / ".jarvis-worktree-owner").write_text(str(os.getpid()), encoding="ascii")

    validator = OwnCodeWorktreeValidator(repo)
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str, cwd=None):
        calls.append(tuple(args))
        if args == ("worktree", "list", "--porcelain"):
            return _completed(args, stdout=f"worktree {worktree}\nHEAD deadbeef\ndetached\n")
        return _completed(args)

    monkeypatch.setattr("artmach_assistant.core.own_code_worktree.tempfile.gettempdir", lambda: str(temp_root))
    monkeypatch.setattr(validator, "_git", fake_git)

    recovered = validator.recover_stale_worktrees()

    assert recovered == ()
    assert parent.is_dir()
    assert not any(call[:2] == ("worktree", "remove") for call in calls)


def test_unmarked_temp_directory_is_never_removed(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    parent = temp_root / "jarvis-own-code-worktree-unmarked"
    parent.mkdir()
    validator = OwnCodeWorktreeValidator(repo)

    monkeypatch.setattr("artmach_assistant.core.own_code_worktree.tempfile.gettempdir", lambda: str(temp_root))
    monkeypatch.setattr(
        validator,
        "_git",
        lambda *args, **kwargs: _completed(tuple(args)),
    )

    assert validator.recover_stale_worktrees() == ()
    assert parent.is_dir()


def test_validate_recovers_stale_worktrees_before_new_worktree() -> None:
    source = Path("core/own_code_worktree.py").read_text(encoding="utf-8")
    recovery = source.index("self.recover_stale_worktrees()")
    clean = source.index("self._require_clean_repository()", recovery)
    created = source.index('tempfile.mkdtemp(prefix="jarvis-own-code-worktree-")', clean)
    assert recovery < clean < created
    assert '.jarvis-worktree-owner' in source[created:]
