from __future__ import annotations

from types import MethodType, SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _finding(
    finding_id: str,
    *,
    category: str,
    error_type: str,
):
    return SimpleNamespace(
        finding_id=finding_id,
        category=category,
        error_type=error_type,
    )


def _engine(report, review, prepared):
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.project_improvements = None
    engine._development_root = MethodType(
        lambda self, *, own_code: "C:/stage4",
        engine,
    )
    engine._runtime_health_service = MethodType(
        lambda self: SimpleNamespace(analyze=lambda **kwargs: report),
        engine,
    )
    engine._maintenance_service = MethodType(
        lambda self: SimpleNamespace(evaluate=lambda *args, **kwargs: review),
        engine,
    )

    def _prepare(self, finding):
        prepared.append(finding.finding_id if finding is not None else None)
        if finding is not None and finding.finding_id == "RUN-REPAIRABLE":
            return SimpleNamespace(plan_id="RPR-REPAIRABLE")
        return None

    engine._prepare_automatic_runtime_failure_entry = MethodType(_prepare, engine)
    return engine


def test_repairable_alert_is_selected_even_when_older_alert_is_first() -> None:
    old_finding = _finding(
        "RUN-OLD",
        category="repeated_runtime_failure",
        error_type="RuntimeError",
    )
    repairable = _finding(
        "RUN-REPAIRABLE",
        category="repeated_runtime_failure",
        error_type="ImportError",
    )
    findings = {
        old_finding.finding_id: old_finding,
        repairable.finding_id: repairable,
    }
    report = SimpleNamespace(
        finding=lambda finding_id: findings.get(finding_id),
    )
    old_alert = SimpleNamespace(
        finding_id="RUN-OLD",
        title="old runtime failure",
        evidence_summary="5 repeats",
    )
    repairable_alert = SimpleNamespace(
        finding_id="RUN-REPAIRABLE",
        title="repairable import failure",
        evidence_summary="3 repeats",
    )
    review = SimpleNamespace(new_alerts=(old_alert, repairable_alert))
    prepared = []
    engine = _engine(report, review, prepared)

    output = engine._automatic_maintenance_note()

    assert prepared == ["RUN-REPAIRABLE"]
    assert "RUN-REPAIRABLE" in output
    assert "RPR-REPAIRABLE" in output
    assert "RUN-OLD" not in output


def test_first_alert_remains_fallback_when_no_repairable_alert_exists() -> None:
    first = _finding(
        "RUN-FIRST",
        category="repeated_runtime_failure",
        error_type="RuntimeError",
    )
    second = _finding(
        "RUN-SECOND",
        category="repeated_slow_operation",
        error_type="",
    )
    findings = {
        first.finding_id: first,
        second.finding_id: second,
    }
    report = SimpleNamespace(
        finding=lambda finding_id: findings.get(finding_id),
    )
    first_alert = SimpleNamespace(
        finding_id="RUN-FIRST",
        title="first alert",
        evidence_summary="5 repeats",
    )
    second_alert = SimpleNamespace(
        finding_id="RUN-SECOND",
        title="second alert",
        evidence_summary="slow",
    )
    review = SimpleNamespace(new_alerts=(first_alert, second_alert))
    prepared = []
    engine = _engine(report, review, prepared)

    output = engine._automatic_maintenance_note()

    assert prepared == ["RUN-FIRST"]
    assert "RUN-FIRST" in output
    assert "RUN-SECOND" not in output
    assert "D\u00fczeltme otomatik uygulanmad\u0131" in output
