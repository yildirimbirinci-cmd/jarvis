from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from artmach_assistant.core.approval_gate import PromotionCommitApprovalGate


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


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "jarvis@example.invalid")
    git(repo, "config", "user.name", "Jarvis Test")
    target = repo / "core" / "example.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", "core/example.py")
    git(repo, "commit", "-m", "initial")
    target.write_text("VALUE = 2\n", encoding="utf-8")

    result_dir = tmp_path / "runtime" / "experiment"
    result_dir.mkdir(parents=True)
    promotion_path = result_dir / "promotion.json"
    promotion_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "promotion_id": "promo1-test",
                "experiment_id": "exp1-test",
                "candidate_id": "candidate-1",
                "status": "promoted",
                "project_root": str(repo),
                "rolled_back": False,
                "files": [
                    {
                        "relative_path": "core/example.py",
                        "before_digest": "0" * 64,
                        "after_digest": digest(target),
                        "checkpoint_path": str(result_dir / "checkpoint.py"),
                    }
                ],
                "commands": [
                    {"name": "focused_tests", "exit_code": 0, "output": "1 passed in 0.01s"},
                    {"name": "full_tests", "exit_code": 0, "output": "100 passed in 1.00s"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return promotion_path, target


def test_explicit_approval_commits_only_promoted_paths(tmp_path: Path) -> None:
    promotion_path, _target = fixture(tmp_path)
    repo = Path(json.loads(promotion_path.read_text())["project_root"])
    (repo / "unrelated.txt").write_text("leave me\n", encoding="utf-8")
    gate = PromotionCommitApprovalGate(promotion_path)
    proposal = gate.prepare("Promote verified experiment")
    assert proposal.paths == ("core/example.py",)
    assert "1 passed" in proposal.focused_test_summary
    result = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    assert result.status == "committed"
    assert result.push_performed is False
    assert git(repo, "show", "--pretty=", "--name-only", result.commit) == "core/example.py"
    assert "unrelated.txt" in git(repo, "status", "--porcelain")


def test_denial_cancels_without_commit(tmp_path: Path) -> None:
    promotion_path, _target = fixture(tmp_path)
    repo = Path(json.loads(promotion_path.read_text())["project_root"])
    before = git(repo, "rev-parse", "HEAD")
    gate = PromotionCommitApprovalGate(promotion_path)
    proposal = gate.prepare("Declined promotion")
    result = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=False)
    assert result.status == "cancelled"
    assert git(repo, "rev-parse", "HEAD") == before


def test_token_is_single_use(tmp_path: Path) -> None:
    promotion_path, _target = fixture(tmp_path)
    gate = PromotionCommitApprovalGate(promotion_path)
    proposal = gate.prepare("Single use")
    first = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    second = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    assert first.status == "committed"
    assert second.status == "invalid"


def test_head_change_blocks_commit(tmp_path: Path) -> None:
    promotion_path, _target = fixture(tmp_path)
    repo = Path(json.loads(promotion_path.read_text())["project_root"])
    gate = PromotionCommitApprovalGate(promotion_path)
    proposal = gate.prepare("Stale proposal")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    git(repo, "add", "other.txt")
    git(repo, "commit", "-m", "other")
    result = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    assert result.status == "head_changed"


def test_working_tree_change_blocks_commit(tmp_path: Path) -> None:
    promotion_path, target = fixture(tmp_path)
    gate = PromotionCommitApprovalGate(promotion_path)
    proposal = gate.prepare("Protected proposal")
    target.write_text("VALUE = 3\n", encoding="utf-8")
    result = gate.approve(proposal.operation_id, proposal.confirmation_token, approved=True)
    assert result.status == "working_tree_changed"


def test_receipt_does_not_store_plain_token(tmp_path: Path) -> None:
    promotion_path, _target = fixture(tmp_path)
    gate = PromotionCommitApprovalGate(promotion_path)
    proposal = gate.prepare("Receipt safety")
    payload = json.loads(Path(proposal.receipt_path).read_text(encoding="utf-8"))
    assert "confirmation_token" not in payload
    assert len(payload["confirmation_token_sha256"]) == 64
    assert payload["full_test_summary"].endswith("100 passed in 1.00s")
