from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.maintenance_advisor import MaintenanceAdvisor
from artmach_assistant.core.notification_store import NotificationStore
from artmach_assistant.core.project_improvement_service import (
    ImprovementEvidence,
    ImprovementFinding,
    ProjectImprovementAssessment,
    ProjectProfile,
)
from artmach_assistant.core.runtime_observability import (
    RuntimeEventStore,
    RuntimeHealthAnalyzer,
)


def _runtime_report(tmp_path: Path, count: int):
    event_path = tmp_path / f"events_{count}.json"
    event_path.unlink(missing_ok=True)
    store = RuntimeEventStore(event_path)
    for _index in range(count):
        store.record(
            component="VoiceService",
            action="speak",
            status="failed",
            workspace=tmp_path,
            scope="own_code",
            source_path="core/voice_service.py",
            symbol="VoiceService.speak",
            message="invalid sample rate",
            error_type="PortAudioError",
        )
    return RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)


def _architecture_assessment(tmp_path: Path) -> ProjectImprovementAssessment:
    finding = ImprovementFinding(
        finding_id="ARC-123456789A",
        severity="high",
        category="dependency_cycle",
        title="Dependency cycle",
        explanation="a.py and b.py import each other.",
        confidence=0.91,
        evidence=(
            ImprovementEvidence(
                source="dependency_graph",
                path="a.py",
                line=1,
                detail="a.py -> b.py",
                metric="cycle_edge",
            ),
        ),
        affected_paths=("a.py", "b.py"),
        recommendation="Make the dependency direction one-way.",
        acceptance_criteria=("The cycle must disappear.",),
        research_query="Python dependency cycle official guidance",
    )
    return ProjectImprovementAssessment(
        root=str(tmp_path),
        generated_at="2026-07-31T00:00:00+00:00",
        profile=ProjectProfile(
            languages=(("Python", 2),),
            frameworks=("pytest",),
            manifests=("pyproject.toml",),
            source_files=2,
            test_files=1,
        ),
        findings=(finding,),
        scanned_files=3,
    )


def test_advisor_notifies_only_new_or_escalated_runtime_evidence(tmp_path: Path) -> None:
    notifications = NotificationStore(tmp_path / "notifications.json")
    advisor = MaintenanceAdvisor(tmp_path / "state.json", notifications)

    first = advisor.evaluate(_runtime_report(tmp_path, 3))
    second = advisor.evaluate(_runtime_report(tmp_path, 3))
    escalated = advisor.evaluate(_runtime_report(tmp_path, 6))

    assert len(first.new_alerts) == 1
    assert second.new_alerts == ()
    assert len(escalated.new_alerts) == 1
    assert len(notifications.load()) == 2


def test_advisor_combines_runtime_and_static_architecture_findings(tmp_path: Path) -> None:
    advisor = MaintenanceAdvisor(tmp_path / "state.json")

    review = advisor.evaluate(
        _runtime_report(tmp_path, 3),
        architecture_assessment=_architecture_assessment(tmp_path),
        notify=False,
    )

    assert {item.source for item in review.active_alerts} == {"runtime", "architecture"}
    assert "RUN-" in review.report()
    assert "ARC-123456789A" in review.report()


def test_acknowledgement_suppresses_same_signature(tmp_path: Path) -> None:
    advisor = MaintenanceAdvisor(tmp_path / "state.json")
    first = advisor.evaluate(_runtime_report(tmp_path, 3), notify=False)
    finding_id = first.active_alerts[0].finding_id

    assert advisor.acknowledge(finding_id) is True
    repeated = advisor.evaluate(_runtime_report(tmp_path, 3), notify=False)
    assert repeated.new_alerts == ()
