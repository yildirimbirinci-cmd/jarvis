from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSISTANT = (ROOT / "core" / "assistant.py").read_text(encoding="utf-8")
SESSION = (ROOT / "core" / "self_repair_session.py").read_text(encoding="utf-8")


def test_approval_required_can_prepare_plan_but_cannot_auto_continue() -> None:
    assert "if not decision.can_prepare_plan:" in ASSISTANT
    assert "if decision.approval_required:" in ASSISTANT
    assert "requires explicit plan approval" in ASSISTANT
    assert "_self_repair_explicit_plan_approval_intent" in ASSISTANT


def test_policy_retry_limit_reaches_model_proposal_generator() -> None:
    assert "repair_max_attempts: int = 3" in ASSISTANT
    assert "max_attempts=repair_max_attempts" in ASSISTANT
    assert "repair_max_attempts=generating.max_attempts" in ASSISTANT
    assert "session.attempts >= session.max_attempts" in ASSISTANT


def test_policy_metadata_is_persisted_in_self_repair_session() -> None:
    for token in (
        "policy_status",
        "risk",
        "max_attempts",
        "approval_required",
        "approval_granted",
        "def grant_approval",
    ):
        assert token in SESSION
