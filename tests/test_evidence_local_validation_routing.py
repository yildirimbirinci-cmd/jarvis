from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.runtime_observability import RuntimeFinding


def _finding():
    return RuntimeFinding(
        "RUN-06578E9EDE", "high", "slow_operation", "slow", "slow", 0.9, 43,
        "2026-08-06T20:59:03+00:00", "", "task",
        ("core/task_orchestrator.py",), ("TaskOrchestrator.wrap.execute",),
        (), "measure", (), "",
    )


def test_local_validation_routes_before_negative_patch_phrase():
    engine = AssistantEngine.__new__(AssistantEngine)
    engine._find_runtime_finding = lambda run_id: _finding()
    engine._runtime_finding_local_validation = lambda finding: "LOCAL-VALIDATION-OK"
    result = engine._reserved_self_repair_request(
        "RUN-06578E9EDE icin LOCAL_VALIDATION calistir. "
        "action_duration_ms ve wrapper_overhead_ms olcumlerini topla. "
        "Hicbir kodu degistirme ve patch taslagi uretme."
    )
    assert result == "LOCAL-VALIDATION-OK"
