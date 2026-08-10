from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSISTANT = ROOT / "core" / "assistant.py"
POLICY = ROOT / "core" / "autonomous_repair_policy.py"


def test_apply_path_revalidates_current_policy_before_fingerprint_apply() -> None:
    text = ASSISTANT.read_text(encoding="utf-8")
    start = text.index("    def _apply_active_self_repair_proposal(")
    end = text.index("\n    def ", start + 10)
    block = text[start:end]
    assert "validate_runtime_repair_enforcement(" in block
    assert "self._assess_runtime_repair_with_target_refresh(finding)" in block
    assert block.index("validate_runtime_repair_enforcement(") < block.index(
        "self.apply_pending_own_code_proposal()"
    )


def test_apply_enforcement_rejects_pending_proposal_on_failure() -> None:
    text = ASSISTANT.read_text(encoding="utf-8")
    assert "if not enforcement.allowed:" in text
    assert "self.editor.reject()" in text
    assert "Policy enforcement blocked targeted repair apply:" in text


def test_policy_module_exposes_pure_enforcement_validator() -> None:
    text = POLICY.read_text(encoding="utf-8")
    assert "class AutonomousRepairEnforcement:" in text
    assert "def validate_runtime_repair_enforcement(" in text
    assert "Persisted retry limit does not match the current decision." in text
    assert "Pending proposal exceeds policy path scope:" in text
