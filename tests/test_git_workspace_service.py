from pathlib import Path
import json
import subprocess
import pytest

from artmach_assistant.core.git_workspace_service import GitWorkspaceError, GitWorkspaceService


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "jarvis@example.invalid")
    git(repo, "config", "user.name", "Jarvis Test")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def test_status_and_diff(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    service = GitWorkspaceService(repo)
    status = service.status()
    assert "a.txt" in status.modified
    assert "new.txt" in status.untracked
    assert "+two" in service.diff(path="a.txt")


def test_snapshot_contains_hash_and_status(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    snapshot = GitWorkspaceService(repo).create_snapshot(tmp_path / "snapshots")
    payload = json.loads(snapshot.manifest_file.read_text(encoding="utf-8"))
    assert payload["workspace"] == str(repo.resolve())
    assert len(payload["diff_sha256"]) == 64
    assert snapshot.diff_file.exists()


def test_snapshot_cannot_be_inside_workspace(tmp_path: Path):
    repo = make_repo(tmp_path)
    with pytest.raises(GitWorkspaceError):
        GitWorkspaceService(repo).create_snapshot(repo / ".snapshots")


def test_rejects_non_git_directory(tmp_path: Path):
    with pytest.raises(GitWorkspaceError):
        GitWorkspaceService(tmp_path)
