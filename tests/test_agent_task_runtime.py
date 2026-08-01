import time

import pytest

from artmach_assistant.core.agent_task_runtime import (
    AgentTaskError,
    AgentTaskRuntime,
    TaskRequest,
    TaskState,
)
from artmach_assistant.core.tool_registry import PermissionLevel, ToolDefinition, ToolRegistry


def make_runtime() -> AgentTaskRuntime:
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        "read_value", "read", PermissionLevel.READ,
        lambda ctx, value: value,
    ))
    registry.register(ToolDefinition(
        "change_value", "change", PermissionLevel.CHANGE,
        lambda ctx, value: value * 2,
    ))
    return AgentTaskRuntime(registry, max_workers=2)


def test_read_tool_runs_without_approval():
    runtime = make_runtime()
    try:
        prepared = runtime.prepare(TaskRequest(
            "read_value", {"value": 7}, PermissionLevel.READ
        ))
        assert prepared.approval_token is None
        result = runtime.wait(prepared.task_id, timeout=2)
        assert result.state is TaskState.SUCCEEDED
        assert result.result == 7
    finally:
        runtime.close()


def test_change_tool_requires_one_time_approval():
    runtime = make_runtime()
    try:
        prepared = runtime.prepare(TaskRequest(
            "change_value", {"value": 5}, PermissionLevel.CHANGE
        ))
        assert prepared.state is TaskState.PENDING_APPROVAL
        with pytest.raises(AgentTaskError, match="onaylanmadı"):
            runtime.wait(prepared.task_id)
        with pytest.raises(AgentTaskError, match="geçersiz"):
            runtime.approve(prepared.task_id, "wrong")
        runtime.approve(prepared.task_id, prepared.approval_token or "")
        snapshot = runtime.wait(prepared.task_id, timeout=2)
        assert snapshot.state is TaskState.SUCCEEDED
        assert snapshot.result == 10
        with pytest.raises(AgentTaskError, match="onay beklemiyor"):
            runtime.approve(prepared.task_id, prepared.approval_token or "")
    finally:
        runtime.close()


def test_requested_permission_cannot_be_lower_than_tool_requirement():
    runtime = make_runtime()
    try:
        with pytest.raises(AgentTaskError, match="karşılamıyor"):
            runtime.prepare(TaskRequest(
                "change_value", {"value": 5}, PermissionLevel.READ
            ))
    finally:
        runtime.close()


def test_progress_is_visible_while_running():
    registry = ToolRegistry()

    def work(ctx, steps):
        for index in range(steps):
            ctx.report_progress("dosyalar işleniyor", index + 1, steps, f"{index + 1}/{steps}")
            time.sleep(0.01)
        return "ok"

    registry.register(ToolDefinition("progress_work", "x", PermissionLevel.READ, work))
    runtime = AgentTaskRuntime(registry)
    try:
        prepared = runtime.prepare(TaskRequest("progress_work", {"steps": 5}))
        observed = False
        for _ in range(50):
            snapshot = runtime.status(prepared.task_id)
            if snapshot.progress.current:
                observed = True
                break
            time.sleep(0.005)
        final = runtime.wait(prepared.task_id, timeout=2)
        assert observed
        assert final.state is TaskState.SUCCEEDED
        assert final.progress.percent == 100
    finally:
        runtime.close()


def test_running_task_can_be_cancelled_cooperatively():
    registry = ToolRegistry()

    def long_work(ctx):
        for index in range(100):
            ctx.raise_if_cancelled()
            ctx.report_progress("çalışıyor", index, 100)
            time.sleep(0.005)
        return "unexpected"

    registry.register(ToolDefinition("long_work", "x", PermissionLevel.READ, long_work))
    runtime = AgentTaskRuntime(registry)
    try:
        prepared = runtime.prepare(TaskRequest("long_work"))
        time.sleep(0.03)
        assert runtime.cancel(prepared.task_id)
        final = runtime.wait(prepared.task_id, timeout=2)
        assert final.state is TaskState.CANCELLED
        assert final.result is None
    finally:
        runtime.close()


def test_pending_approval_task_can_be_cancelled_without_execution():
    called = False
    registry = ToolRegistry()

    def mutating(ctx):
        nonlocal called
        called = True

    registry.register(ToolDefinition(
        "mutating", "x", PermissionLevel.CHANGE, mutating, destructive=True
    ))
    runtime = AgentTaskRuntime(registry)
    try:
        prepared = runtime.prepare(TaskRequest(
            "mutating", requested_permission=PermissionLevel.CHANGE
        ))
        assert runtime.cancel(prepared.task_id)
        assert runtime.status(prepared.task_id).state is TaskState.CANCELLED
        assert not called
    finally:
        runtime.close()


def test_failed_tool_records_error_without_crashing_runtime():
    registry = ToolRegistry()

    def fail(ctx):
        raise ValueError("boom")

    registry.register(ToolDefinition("failure", "x", PermissionLevel.READ, fail))
    runtime = AgentTaskRuntime(registry)
    try:
        prepared = runtime.prepare(TaskRequest("failure"))
        final = runtime.wait(prepared.task_id, timeout=2)
        assert final.state is TaskState.FAILED
        assert "ValueError: boom" == final.error
        assert len(runtime.list_tasks()) == 1
        assert runtime.prune_finished() == 1
    finally:
        runtime.close()
