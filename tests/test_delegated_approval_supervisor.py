from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.self_improvement_supervisor import SelfImprovementSupervisor


def test_delegation_metadata_flows_from_cycle_to_approval(tmp_path: Path) -> None:
    supervisor = SelfImprovementSupervisor(
        tmp_path,
        cycle_handler=lambda payload: SimpleNamespace(
            status="completed",
            artifact_path=str(tmp_path / "experiment.json"),
            candidate_id="candidate-1",
        ),
        promotion_handler=lambda payload: SimpleNamespace(
            status="promoted",
            artifact_path=str(tmp_path / "promotion.json"),
            candidate_id="candidate-1",
        ),
        approval_handler=lambda payload: SimpleNamespace(status="waiting_approval"),
        idle_seconds=0,
    )
    supervisor.enqueue_cycle({
        "delegated_policy_path": str(tmp_path / "policy.json"),
        "domain": "voice",
        "diagnostic_report_path": str(tmp_path / "diagnostic.json"),
        "commit_message": "Night voice improvement",
    })
    assert supervisor.tick().status == "completed"
    promotion = supervisor.scheduler.next_pending()
    assert promotion is not None
    assert promotion.payload["domain"] == "voice"
    assert supervisor.tick().status == "completed"
    approval = supervisor.scheduler.next_pending()
    assert approval is not None
    assert approval.kind == "approval"
    assert approval.payload["delegated_policy_path"].endswith("policy.json")
    assert approval.payload["commit_message"] == "Night voice improvement"
