from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.own_code_worktree import OwnCodeWorktreeValidator


def test_dead_owner_managed_worktree_is_removed(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    parent = temp_root / "jarvis-own-code-worktree-dead"
    worktree = parent / repo.name
    worktree.mkdir(parents=True)
    (parent / ".jarvis-worktree-owner.json").write_text(
        json.dumps({"pid": 424242, "root": str(repo.resolve())}),
        encoding="utf-8",
    )

    validator = OwnCodeWorktreeValidator(repo)
    monkeypatch.setattr(
        "artmach_assistant.core.own_code_worktree.tempfile.gettempdir",
        lambda: str(temp_root),
    )
    monkeypatch.setattr(validator, "_pid_alive", lambda pid: False)

    git_calls = []

    def fake_git(*args, **kwargs):
        git_calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(validator, "_git", fake_git)

    validator.cleanup_stale_managed_worktrees()

    assert not parent.exists()
    assert any(call[:3] == ("worktree", "remove", "--force") for call in git_calls)
    assert any(call[:2] == ("worktree", "prune") for call in git_calls)


def test_live_owner_managed_worktree_is_preserved(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    parent = temp_root / "jarvis-own-code-worktree-live"
    (parent / repo.name).mkdir(parents=True)
    (parent / ".jarvis-worktree-owner.json").write_text(
        json.dumps({"pid": 123, "root": str(repo.resolve())}),
        encoding="utf-8",
    )

    validator = OwnCodeWorktreeValidator(repo)
    monkeypatch.setattr(
        "artmach_assistant.core.own_code_worktree.tempfile.gettempdir",
        lambda: str(temp_root),
    )
    monkeypatch.setattr(validator, "_pid_alive", lambda pid: True)

    def forbidden_git(*args, **kwargs):
        raise AssertionError("live worktree must not be removed")

    monkeypatch.setattr(validator, "_git", forbidden_git)

    validator.cleanup_stale_managed_worktrees()

    assert parent.exists()


def test_foreign_repository_marker_is_preserved(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    parent = temp_root / "jarvis-own-code-worktree-foreign"
    (parent / repo.name).mkdir(parents=True)
    (parent / ".jarvis-worktree-owner.json").write_text(
        json.dumps({"pid": 999, "root": str((tmp_path / "other").resolve())}),
        encoding="utf-8",
    )

    validator = OwnCodeWorktreeValidator(repo)
    monkeypatch.setattr(
        "artmach_assistant.core.own_code_worktree.tempfile.gettempdir",
        lambda: str(temp_root),
    )
    monkeypatch.setattr(validator, "_pid_alive", lambda pid: False)

    validator.cleanup_stale_managed_worktrees()

    assert parent.exists()


def test_validate_writes_owner_marker_before_worktree_creation() -> None:
    source = Path("core/own_code_worktree.py").read_text(encoding="utf-8")
    marker_pos = source.find("self._write_owner_marker(parent)")
    add_pos = source.find('self._git("worktree", "add"')
    assert marker_pos >= 0
    assert add_pos >= 0
    assert marker_pos < add_pos


def test_validate_runs_stale_cleanup_before_new_tempdir() -> None:
    source = Path("core/own_code_worktree.py").read_text(encoding="utf-8")
    cleanup_pos = source.find("self.cleanup_stale_managed_worktrees()")
    temp_pos = source.find('tempfile.mkdtemp(prefix="jarvis-own-code-worktree-")')
    assert cleanup_pos >= 0
    assert temp_pos >= 0
    assert cleanup_pos < temp_pos
