from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.runtime_observability import RuntimeHealthReport


def test_exact_maintenance_command_routes_to_finite_session() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.run_one_shot_autonomous_maintenance = lambda: "FINITE-MAINTENANCE"

    rendered = engine._reserved_self_repair_request(
        "Kendinde gordugun hata ve eksikleri gider."
    )

    assert rendered == "FINITE-MAINTENANCE"


def test_development_command_does_not_start_maintenance_session() -> None:
    assert not AssistantEngine._asks_for_one_shot_maintenance(
        "Kendini gelistir"
    )


def test_empty_runtime_report_finishes_without_repair() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    report = RuntimeHealthReport(
        generated_at="now",
        workspace="root",
        lookback_hours=168,
        event_count=0,
        completed_count=0,
        failed_count=0,
        cancelled_count=0,
        warning_count=0,
        findings=(),
    )
    engine.maintenance_review = lambda **kwargs: "review"
    engine.runtime_health_assessment = lambda **kwargs: report

    rendered = engine.run_one_shot_autonomous_maintenance()

    assert "Incelenen bulgu: 0" in rendered
    assert "Surekli gelisim modu baslatilmadi" in rendered
