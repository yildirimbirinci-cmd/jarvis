from artmach_assistant.core.evidence_local_validation import (
    build_local_runtime_validation,
)
from artmach_assistant.core.runtime_observability import RuntimeEvent, RuntimeFinding


def finding() -> RuntimeFinding:
    return RuntimeFinding(
        "RUN-X", "high", "slow_operation", "slow", "slow", 0.9, 4,
        "2026-08-07T08:00:00+00:00", "C:/repo", "task",
        ("core/task_orchestrator.py",), ("TaskOrchestrator.wrap.execute",),
        (), "measure", (), "",
    )


def event(index: int, *, path: str = "", symbol: str = "") -> RuntimeEvent:
    metadata = {
        "action_started": True,
        "action_completed": True,
        "action_duration_ms": 1000.0 + index,
        "wrapper_overhead_ms": 0.4,
    }
    if path:
        metadata["action_path"] = path
    if symbol:
        metadata["action_symbol"] = symbol
    return RuntimeEvent(
        f"e{index}", f"2026-08-07T08:00:{index:02d}+00:00",
        "TaskOrchestrator", "execute_task", "completed", 1001.0 + index,
        "C:/repo", "task", "core/task_orchestrator.py",
        "TaskOrchestrator.wrap.execute", metadata=metadata,
    )


def test_local_validation_reports_dominant_action_identity() -> None:
    report = build_local_runtime_validation(
        finding(),
        (
            event(1, path="core/assistant.py", symbol="AssistantEngine.work"),
            event(2, path="core/assistant.py", symbol="AssistantEngine.work"),
            event(3, path="core/other.py", symbol="Other.work"),
        ),
    )
    assert report.action_target_path == "core/assistant.py"
    assert report.action_target_symbol == "AssistantEngine.work"
    assert report.action_identity_samples == 2
    rendered = report.report()
    assert rendered.startswith("YEREL RUNTIME DOGRULAMA")
    assert "Action hedefi: core/assistant.py - AssistantEngine.work" in rendered
    assert "Bir sonraki yerel hedef: core/assistant.py - AssistantEngine.work" in rendered


def test_old_events_do_not_guess_action_identity() -> None:
    report = build_local_runtime_validation(
        finding(),
        (event(1), event(2), event(3)),
    )
    assert report.action_target_path == ""
    assert "yeni kimlikli runtime ornekleri gerekli" in report.conclusion
