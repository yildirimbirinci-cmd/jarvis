from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from artmach_assistant.core.push_gate import OptionalPushApprovalGate, PushGateError


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def fixture(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "jarvis@example.invalid")
    git(repo, "config", "user.name", "Jarvis Test")
    git(repo, "checkout", "-b", "main")
    (repo / "example.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "example.txt")
    git(repo, "commit", "-m", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


def remote_head(remote: Path, branch: str = "main") -> str:
    completed = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def test_explicit_push_approval_updates_remote(tmp_path: Path) -> None:
    repo, remote = fixture(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    gate = OptionalPushApprovalGate(repo)
    proposal = gate.prepare(commit=head)
    result = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    assert result.status == "pushed"
    assert result.pushed is True
    assert remote_head(remote) == head


def test_denial_does_not_push(tmp_path: Path) -> None:
    repo, remote = fixture(tmp_path)
    gate = OptionalPushApprovalGate(repo)
    proposal = gate.prepare(commit=git(repo, "rev-parse", "HEAD"))
    result = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=False)
    assert result.status == "cancelled"
    completed = subprocess.run(
        ["git", "--git-dir", str(remote), "show-ref", "--verify", "refs/heads/main"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert completed.returncode != 0


def test_push_token_is_single_use(tmp_path: Path) -> None:
    repo, _remote = fixture(tmp_path)
    gate = OptionalPushApprovalGate(repo)
    proposal = gate.prepare(commit=git(repo, "rev-parse", "HEAD"))
    first = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    second = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    assert first.status == "pushed"
    assert second.status == "invalid"


def test_head_change_blocks_push(tmp_path: Path) -> None:
    repo, remote = fixture(tmp_path)
    gate = OptionalPushApprovalGate(repo)
    proposal = gate.prepare(commit=git(repo, "rev-parse", "HEAD"))
    (repo / "two.txt").write_text("two\n", encoding="utf-8")
    git(repo, "add", "two.txt")
    git(repo, "commit", "-m", "second")
    result = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    assert result.status == "head_changed"
    completed = subprocess.run(["git", "--git-dir", str(remote), "show-ref"], check=False)
    assert completed.returncode == 1


def test_working_tree_change_blocks_push(tmp_path: Path) -> None:
    repo, _remote = fixture(tmp_path)
    gate = OptionalPushApprovalGate(repo)
    proposal = gate.prepare(commit=git(repo, "rev-parse", "HEAD"))
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    result = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    assert result.status == "working_tree_changed"


def test_prepare_requires_clean_current_head_and_safe_receipt(tmp_path: Path) -> None:
    repo, _remote = fixture(tmp_path)
    gate = OptionalPushApprovalGate(repo)
    head = git(repo, "rev-parse", "HEAD")
    proposal = gate.prepare(commit=head)
    payload = json.loads(Path(proposal.receipt_path).read_text(encoding="utf-8"))
    assert "confirmation_token" not in payload
    assert len(payload["confirmation_token_sha256"]) == 64
    gate.cancel(proposal.operation_id)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(PushGateError, match="clean"):
        gate.prepare(commit=head)
