from artmach_assistant.core.evidence_local_validation import build_local_runtime_validation
from artmach_assistant.core.runtime_observability import RuntimeEvent, RuntimeFinding


def _finding():
    return RuntimeFinding(
        "RUN-06578E9EDE", "high", "slow_operation", "slow", "slow", 0.9, 4,
        "2026-08-06T20:59:03+00:00", "C:/repo", "task",
        ("core/task_orchestrator.py",), ("TaskOrchestrator.wrap.execute",),
        (), "measure", (), "",
    )


def _event(i, action, wrapper):
    return RuntimeEvent(
        f"e{i}", f"2026-08-06T20:59:{i:02d}+00:00",
        "TaskOrchestrator", "execute_task", "completed", action + wrapper,
        "C:/repo", "task", "core/task_orchestrator.py",
        "TaskOrchestrator.wrap.execute",
        metadata={
            "action_started": True,
            "action_completed": True,
            "action_duration_ms": action,
            "wrapper_overhead_ms": wrapper,
        },
    )


def test_action_dominance():
    report = build_local_runtime_validation(
        _finding(),
        (_event(1,100,.4), _event(2,120,.5), _event(3,110,.6), _event(4,130,.5)),
    )
    assert report.locally_confirmed
    assert report.sample_count == 4
    assert report.action_median_ms == 115.0
    assert report.wrapper_median_ms == 0.5
    assert "Darbogaz wrapper katmaninda degil" in report.report()
    assert "Patch taslagi uretilmedi." in report.report()


def test_insufficient_samples():
    report = build_local_runtime_validation(_finding(), (_event(1,10,1),))
    assert not report.locally_confirmed
