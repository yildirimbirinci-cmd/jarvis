from __future__ import annotations

from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)
from artmach_assistant.core.evidence_research import (
    BLOCKED,
    EXTERNAL_APPROVAL_REQUIRED,
    LOCAL_REVIEW,
    NOT_NEEDED,
    build_evidence_research_plan,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_BLOCKED,
    RETEST_FAILED,
    RETEST_PASSED,
    RetestExecutionResult,
)


def _finding(
    *,
    lifecycle: str = "ACTIVE",
    score: int = 90,
) -> EvidenceMaintenanceFinding:
    return EvidenceMaintenanceFinding(
        classification="A",
        score=score,
        source="runtime",
        title="Tekrarlanan hata: Example.run",
        path="core/example.py",
        symbol="Example.run",
        evidence="4 tekrar",
        repair_candidate=True,
        lifecycle=lifecycle,
    )


def _result(status: str) -> RetestExecutionResult:
    return RetestExecutionResult(
        status=status,
        title="Example.run",
        path="core/example.py",
        symbol="Example.run",
        returncode=0 if status == RETEST_PASSED else 1,
        reason="test sonucu",
    )


def test_passed_retest_does_not_require_research() -> None:
    plan = build_evidence_research_plan(
        _finding(lifecycle="NEEDS_RETEST"),
        retest_result=_result(RETEST_PASSED),
    )
    assert plan.status == NOT_NEEDED
    assert plan.external_queries == ()
    assert plan.requires_external_approval is False


def test_failed_retest_requires_external_approval() -> None:
    plan = build_evidence_research_plan(
        _finding(lifecycle="NEEDS_RETEST"),
        retest_result=_result(RETEST_FAILED),
    )
    assert plan.status == EXTERNAL_APPROVAL_REQUIRED
    assert plan.requires_external_approval is True
    assert 1 <= len(plan.external_queries) <= 4
    assert "Example.run" in plan.external_queries[0]


def test_blocked_retest_does_not_start_research() -> None:
    plan = build_evidence_research_plan(
        _finding(lifecycle="NEEDS_RETEST"),
        retest_result=_result(RETEST_BLOCKED),
    )

    assert plan.status == BLOCKED
    assert plan.external_queries == ()


def test_active_high_risk_finding_starts_local_review() -> None:
    plan = build_evidence_research_plan(
        _finding(
            lifecycle="ACTIVE",
            score=80,
        )
    )

    assert plan.status == LOCAL_REVIEW
    assert plan.local_questions
    assert plan.external_queries == ()


def test_runtime_task_orchestrator_without_structured_target_is_blocked() -> None:
    finding = EvidenceMaintenanceFinding(
        classification="A",
        score=90,
        source="runtime",
        title=(
            "Tekrarlanan yavas islem: "
            "TaskOrchestrator.execute_task"
        ),
        path="",
        symbol="",
        evidence="Repeated runtime latency.",
        repair_candidate=False,
        lifecycle="ACTIVE",
    )

    plan = build_evidence_research_plan(finding)

    assert plan.status == BLOCKED
    assert plan.path == ""
    assert plan.symbol == ""


def test_existing_location_is_preserved() -> None:
    plan = build_evidence_research_plan(_finding())

    assert plan.path == "core/example.py"
    assert plan.symbol == "Example.run"


def test_low_risk_static_hint_needs_no_research() -> None:
    finding = EvidenceMaintenanceFinding(
        classification="C",
        score=10,
        source="static",
        title="[STYLE] uzun satir",
        path="app.py",
        lifecycle="STATIC",
    )

    plan = build_evidence_research_plan(finding)

    assert plan.status == NOT_NEEDED


def test_report_states_that_internet_has_not_started() -> None:
    plan = build_evidence_research_plan(
        _finding(lifecycle="NEEDS_RETEST"),
        retest_result=_result(RETEST_FAILED),
    )

    rendered = plan.report()

    assert "Internet arastirmasi henuz baslatilmadi" in rendered
    assert "Acik kullanici izni gerekiyor" in rendered
    assert "dogrudan patch olarak uygulanamaz" in rendered
