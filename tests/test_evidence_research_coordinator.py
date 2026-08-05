from __future__ import annotations

from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)
from artmach_assistant.core.evidence_research_coordinator import (
    EXTERNAL_APPROVAL_PENDING,
    LOCAL_PLAN_READY,
    EvidenceResearchCoordinator,
)
from artmach_assistant.core.evidence_research_session import (
    EvidenceResearchApprovalStore,
)


def _finding() -> EvidenceMaintenanceFinding:
    return EvidenceMaintenanceFinding(
        classification="A",
        score=90,
        source="runtime",
        title=(
            "Tekrarlanan yavas islem: "
            "TaskOrchestrator.execute_task"
        ),
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
        evidence="Repeated runtime latency.",
        repair_candidate=False,
        lifecycle="ACTIVE",
    )


def test_coordinator_keeps_local_plan_before_review(
    tmp_path,
) -> None:
    coordinator = EvidenceResearchCoordinator(
        store=EvidenceResearchApprovalStore(
            tmp_path / "pending.json"
        )
    )

    result = coordinator.coordinate(_finding())

    assert result.status == LOCAL_PLAN_READY
    assert result.approval_session is None
    assert "Durum: LOCAL_REVIEW" in result.report


def test_coordinator_promotes_insufficient_local_review(
    tmp_path,
) -> None:
    store = EvidenceResearchApprovalStore(
        tmp_path / "pending.json"
    )
    coordinator = EvidenceResearchCoordinator(
        store=store
    )

    result = coordinator.coordinate(
        _finding(),
        local_review_complete=True,
        local_evidence_sufficient=False,
    )

    assert result.status == EXTERNAL_APPROVAL_PENDING
    assert result.approval_session is not None
    assert result.approval_session.approval_id.startswith(
        "RS-"
    )
    assert "DIS ARASTIRMA ONAYI" in result.report
    assert "core/task_orchestrator.py" in result.report
    assert "TaskOrchestrator.wrap.execute" in result.report
    assert store.load() is not None


def test_coordinator_reuses_same_pending_session(
    tmp_path,
) -> None:
    store = EvidenceResearchApprovalStore(
        tmp_path / "pending.json"
    )
    coordinator = EvidenceResearchCoordinator(
        store=store
    )

    first = coordinator.coordinate(
        _finding(),
        local_review_complete=True,
        local_evidence_sufficient=False,
    )
    second = coordinator.coordinate(
        _finding(),
        local_review_complete=True,
        local_evidence_sufficient=False,
    )

    assert first.approval_session is not None
    assert second.approval_session is not None
    assert (
        first.approval_session.approval_id
        == second.approval_session.approval_id
    )
