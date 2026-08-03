from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.self_improvement_handlers import RuntimeHandlerRegistry


def test_approval_handler_uses_delegated_policy_when_present(tmp_path: Path) -> None:
    promotion = tmp_path / "promotion.json"
    promotion.write_text("{}", encoding="utf-8")
    captured = {}

    class Runtime:
        def execute(self, path, **kwargs):
            captured["path"] = path
            captured.update(kwargs)
            return SimpleNamespace(
                status="committed",
                message="committed under delegation",
                operation_id="op-1",
                audit_path=str(tmp_path / "audit.jsonl"),
            )

    registry = RuntimeHandlerRegistry(
        delegated_approval_factory=lambda policy: Runtime(),
    )
    result = registry.approval({
        "promotion_result_path": str(promotion),
        "delegated_policy_path": str(tmp_path / "policy.json"),
        "domain": "voice",
        "commit_message": "Night voice fix",
        "candidate_id": "candidate-1",
    })
    assert result.status == "committed"
    assert result.operation_id == "op-1"
    assert captured["domain"] == "voice"


def test_delegated_approval_without_domain_waits_for_owner(tmp_path: Path) -> None:
    promotion = tmp_path / "promotion.json"
    promotion.write_text("{}", encoding="utf-8")
    registry = RuntimeHandlerRegistry(
        delegated_approval_factory=lambda policy: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    result = registry.approval({
        "promotion_result_path": str(promotion),
        "delegated_policy_path": str(tmp_path / "policy.json"),
    })
    assert result.status == "waiting_approval"
