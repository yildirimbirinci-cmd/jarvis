from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from artmach_assistant.core.approval_gate import PromotionCommitApprovalGate


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def test_commit_proposal_contains_trust_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "jarvis@example.invalid")
    _git(repo, "config", "user.name", "Jarvis")
    target = repo / "core" / "example.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "core/example.py")
    _git(repo, "commit", "-m", "initial")
    target.write_text("VALUE = 2\n", encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.py"
    checkpoint.write_text("VALUE = 1\n", encoding="utf-8")
    promotion = tmp_path / "promotion.json"
    promotion.write_text(json.dumps({
        "promotion_id": "promo-1", "experiment_id": "exp-1", "candidate_id": "cand-1",
        "status": "promoted", "project_root": str(repo), "risk": "low", "rolled_back": False,
        "checkpoint_root": str(tmp_path),
        "files": [{"relative_path": "core/example.py", "after_digest": hashlib.sha256(target.read_bytes()).hexdigest(), "checkpoint_path": str(checkpoint)}],
        "commands": [
            {"name": "focused_tests", "exit_code": 0, "output": "1 passed"},
            {"name": "full_tests", "exit_code": 0, "output": "100 passed"},
        ],
    }), encoding="utf-8")
    diagnostic = tmp_path / "diagnostic.json"
    diagnostic.write_text(json.dumps({"investigation": {
        "root_cause_hypothesis_id": "h1",
        "hypotheses": [{"hypothesis_id": "h1", "subsystem": "ui", "cause": "layout", "confidence": 95, "evidence_ids": ["e1", "e2"], "explanation": "layout bottleneck"}],
    }}), encoding="utf-8")
    proposal = PromotionCommitApprovalGate(promotion, diagnostic_report_path=diagnostic).prepare("Trust report")
    assert proposal.trust_recommendation == "approve"
    assert Path(proposal.trust_report_path).is_file()
    receipt = json.loads(Path(proposal.receipt_path).read_text(encoding="utf-8"))
    assert receipt["trust_recommendation"] == "approve"


def test_commit_proposal_contains_owner_trust_presentation(tmp_path: Path) -> None:
    repo = tmp_path / "repo-presentation"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "jarvis@example.invalid")
    _git(repo, "config", "user.name", "Jarvis")
    target = repo / "core" / "example.py"
    target.parent.mkdir()
    target.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "core/example.py")
    _git(repo, "commit", "-m", "initial")
    target.write_text("VALUE = 2\n", encoding="utf-8")
    checkpoint = tmp_path / "checkpoint-presentation.py"
    checkpoint.write_text("VALUE = 1\n", encoding="utf-8")
    promotion = tmp_path / "promotion-presentation.json"
    promotion.write_text(json.dumps({
        "promotion_id": "promo-p", "experiment_id": "exp-p", "candidate_id": "cand-p",
        "status": "promoted", "project_root": str(repo), "risk": "low", "rolled_back": False,
        "checkpoint_root": str(tmp_path),
        "files": [{"relative_path": "core/example.py", "after_digest": hashlib.sha256(target.read_bytes()).hexdigest(), "checkpoint_path": str(checkpoint)}],
        "commands": [
            {"name": "focused_tests", "exit_code": 0, "output": "1 passed"},
            {"name": "full_tests", "exit_code": 0, "output": "100 passed"},
        ],
    }), encoding="utf-8")
    proposal = PromotionCommitApprovalGate(promotion).prepare("Presentation")
    assert Path(proposal.trust_presentation_path).is_file()
    assert proposal.trust_summary
    assert proposal.trust_voice_summary
    receipt = json.loads(Path(proposal.receipt_path).read_text(encoding="utf-8"))
    assert receipt["trust_presentation_path"] == proposal.trust_presentation_path
