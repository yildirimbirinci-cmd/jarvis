from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_instrumentation():
    instrumentation = importlib.import_module(
        "artmach_assistant.core.runtime_instrumentation"
    )
    instrumentation.reset_runtime_instrumentation_for_tests()
    yield
    instrumentation.reset_runtime_instrumentation_for_tests()


def _types():
    instrumentation = importlib.import_module(
        "artmach_assistant.core.runtime_instrumentation"
    )
    task_module = importlib.import_module(
        "artmach_assistant.core.task_orchestrator"
    )
    return instrumentation, task_module.TaskOrchestrator


def test_task_event_reports_action_and_wrapper_timing(
    tmp_path: Path,
) -> None:
    instrumentation, TaskOrchestrator = _types()
    events: list[dict[str, object]] = []

    def recorder(**payload):
        events.append(payload)
        return True

    instrumentation.configure_runtime_instrumentation(
        recorder,
        workspace_provider=lambda: tmp_path,
    )
    instrumentation.install_runtime_instrumentation()

    orchestrator = TaskOrchestrator(
        history_file=tmp_path / "task_history.json",
        active_file=tmp_path / "active_task.json",
    )
    record, token = orchestrator.start("timed task", "test")

    def action() -> int:
        time.sleep(0.01)
        return 42

    execute = orchestrator.wrap(record.task_id, token, action)
    assert execute() == 42

    event = next(
        item for item in events
        if item["action"] == "execute_task"
    )
    metadata = event["metadata"]

    assert metadata["action_started"] is True
    assert metadata["action_completed"] is True
    assert metadata["action_duration_ms"] >= 5.0
    assert metadata["wrapper_overhead_ms"] >= 0.0
    assert event["duration_ms"] >= metadata["action_duration_ms"]


def test_cancelled_before_action_reports_zero_action_time(
    tmp_path: Path,
) -> None:
    instrumentation, TaskOrchestrator = _types()
    events: list[dict[str, object]] = []

    def recorder(**payload):
        events.append(payload)
        return True

    instrumentation.configure_runtime_instrumentation(
        recorder,
        workspace_provider=lambda: tmp_path,
    )
    instrumentation.install_runtime_instrumentation()

    orchestrator = TaskOrchestrator(
        history_file=tmp_path / "task_history.json",
        active_file=tmp_path / "active_task.json",
    )
    record, token = orchestrator.start("cancelled task", "test")
    token.cancel()

    execute = orchestrator.wrap(record.task_id, token, lambda: 42)

    with pytest.raises(InterruptedError):
        execute()

    event = next(
        item for item in events
        if item["action"] == "execute_task"
    )
    metadata = event["metadata"]

    assert metadata["action_started"] is False
    assert metadata["action_completed"] is False
    assert metadata["action_duration_ms"] == 0.0
    assert metadata["wrapper_overhead_ms"] >= 0.0
