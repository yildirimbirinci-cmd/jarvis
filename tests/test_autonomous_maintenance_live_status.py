from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.operation_control import OperationController


def test_maintenance_exposes_live_status_while_repair_runs() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.operation_controller = OperationController()
    finding = SimpleNamespace(
        finding_id="RUN-EXAMPLE",
        title="Yavas islem",
        affected_paths=("core/example.py",),
        affected_symbols=("Example.run",),
        category="repeated_slow_operation",
        severity="medium",
        confidence=0.9,
        occurrence_count=5,
        last_seen="now",
    )
    report = SimpleNamespace(findings=(finding,))
    engine.maintenance_review = lambda **kwargs: None
    calls = {"count": 0}

    def health(**kwargs):
        calls["count"] += 1
        return report if calls["count"] == 1 else SimpleNamespace(findings=())

    engine.runtime_health_assessment = health
    engine._runtime_maintenance_priority = lambda item: (1,)

    import artmach_assistant.core.assistant as assistant_module
    original = assistant_module.assess_autonomous_runtime_repair
    assistant_module.assess_autonomous_runtime_repair = lambda item: SimpleNamespace(allowed=True, reason="ok")
    engine._self_repair_store = lambda: SimpleNamespace(
        load=lambda: SimpleNamespace(state="completed", finding_id="RUN-EXAMPLE")
    )

    seen = []
    def repair(_finding_id):
        seen.append(engine.operation_status_report())
        return "done"
    engine.run_autonomous_runtime_repair = repair
    try:
        rendered = engine.run_one_shot_autonomous_maintenance(max_findings=2)
    finally:
        assistant_module.assess_autonomous_runtime_repair = original

    assert seen
    assert "duzeltme deniyorum" in seen[0].casefold()
    assert "Tamamlanan: 1" in rendered
