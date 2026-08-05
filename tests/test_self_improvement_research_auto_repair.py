from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.self_improvement_research import (
    SelfImprovementResearchTask,
)


def _task(*, evidence_ids: tuple[str, ...]) -> SelfImprovementResearchTask:
    return SelfImprovementResearchTask(
        task_id="SIR-EXAMPLE",
        complaint="slow operation",
        state="solution_found",
        created_at="2026-08-05T00:00:00+00:00",
        completed_at="2026-08-05T00:01:00+00:00",
        summary="Measured slowdown.",
        cause="The operation exceeded its threshold.",
        solution="Measure and repair the proven bottleneck.",
        benefit="Lower latency.",
        risk="Wrong target would not help.",
        affected_paths=("core/task_orchestrator.py",),
        evidence_ids=evidence_ids,
    )


class _Store:
    def __init__(self) -> None:
        self.current = None
        self.states: list[str] = []

    def prepare_plan(self, task):
        self.current = replace(
            task,
            plan_options=("safe option",),
            recommended_option="safe option",
            implementation_plan=("validate target",),
            test_plan=("run tests",),
            plan_created_at="now",
        )
        return self.current

    def record_automation_result(self, task, *, state, summary):
        self.states.append(state)
        self.current = replace(
            task,
            automation_state=state,
            automation_summary=summary,
        )
        return self.current

    def load(self, _task_id):
        return self.current


def test_completed_research_continues_to_autonomous_repair() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    store = _Store()
    engine.self_improvement_research = store
    engine.run_autonomous_runtime_repair = lambda finding_id: "repair complete"
    engine._self_repair_store = lambda: SimpleNamespace(
        load=lambda: SimpleNamespace(
            finding_id="RUN-ABC123",
            state="completed",
        )
    )

    result = engine._advance_completed_research_to_repair(
        _task(evidence_ids=("RUN-ABC123",))
    )

    assert store.states == ["running", "completed"]
    assert result.automation_state == "completed"
    assert "başarıyla tamamlandı" in result.automation_summary


def test_completed_research_without_run_target_stops_safely() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    store = _Store()
    engine.self_improvement_research = store

    result = engine._advance_completed_research_to_repair(
        _task(evidence_ids=("ARC-EXAMPLE",))
    )

    assert store.states == ["inconclusive"]
    assert result.automation_state == "inconclusive"
    assert "hiçbir dosya değiştirilmedi" in result.automation_summary


def test_user_report_describes_automatic_maintenance_result() -> None:
    task = replace(
        _task(evidence_ids=("RUN-ABC123",)),
        automation_state="blocked",
        automation_summary="Güvenli çözüm üretilemedi; dosyalar değişmedi.",
    )

    report = task.user_report()

    assert "Plan istersen" not in report
    assert "Self Improvement Planner" not in report
    assert "Bakım zinciri sonucu" in report
    assert "dosyalar değişmedi" in report
