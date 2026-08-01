from pathlib import Path
import json
import subprocess

import pytest

from artmach_assistant.core.git_change_service import GitChangeError, GitChangeService


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


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


def test_prepare_requires_confirmation_and_creates_receipt(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    service = GitChangeService(repo)
    prepared = service.prepare_commit("Update a", tmp_path / "snapshots")
    receipt = Path(prepared.snapshot_directory) / "prepared_commit.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["expected_head"] == prepared.expected_head
    assert "confirmation_token" not in payload
    assert len(payload["confirmation_token_sha256"]) == 64
    with pytest.raises(GitChangeError):
        service.commit(prepared.operation_id, "wrong-token")


def test_confirmed_commit_only_commits_selected_paths(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "b.txt").write_text("unselected\n", encoding="utf-8")
    service = GitChangeService(repo)
    prepared = service.prepare_commit("Selected change", tmp_path / "snapshots", paths=["a.txt"])
    result = service.commit(prepared.operation_id, prepared.confirmation_token)
    assert git(repo, "show", "--pretty=", "--name-only", result.commit) == "a.txt"
    assert "b.txt" in git(repo, "status", "--porcelain")
    with pytest.raises(GitChangeError):
        service.commit(prepared.operation_id, prepared.confirmation_token)


def test_head_change_invalidates_prepared_commit(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    service = GitChangeService(repo)
    prepared = service.prepare_commit("Will be stale", tmp_path / "snapshots")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    git(repo, "add", "other.txt")
    git(repo, "commit", "-m", "other")
    with pytest.raises(GitChangeError, match="HEAD değişti"):
        service.commit(prepared.operation_id, prepared.confirmation_token)


def test_cancel_prevents_commit(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    service = GitChangeService(repo)
    prepared = service.prepare_commit("Cancelled", tmp_path / "snapshots")
    assert service.cancel(prepared.operation_id)
    with pytest.raises(GitChangeError):
        service.commit(prepared.operation_id, prepared.confirmation_token)


def test_revert_creates_history_preserving_commit(tmp_path: Path):
    repo = make_repo(tmp_path)
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    service = GitChangeService(repo)
    prepared = service.prepare_commit("Change to revert", tmp_path / "snapshots")
    committed = service.commit(prepared.operation_id, prepared.confirmation_token)
    reverted = service.revert_commit(committed.commit, expected_head=committed.commit)
    assert reverted.reverted_commit == committed.commit
    assert reverted.revert_commit != committed.commit
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"
    assert len(git(repo, "rev-list", "--count", "HEAD")) > 0


def test_revert_requires_clean_workspace(tmp_path: Path):
    repo = make_repo(tmp_path)
    target = git(repo, "rev-parse", "HEAD")
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(GitChangeError, match="tamamen temiz"):
        GitChangeService(repo).revert_commit(target)


def test_rejects_paths_outside_workspace(tmp_path: Path):
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(GitChangeError, match="dışındaki"):
        GitChangeService(repo).prepare_commit("Bad path", tmp_path / "snapshots", paths=["../outside.txt"])
