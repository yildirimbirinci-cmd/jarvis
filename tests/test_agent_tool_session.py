from __future__ import annotations

import time

import pytest

from artmach_assistant.core.agent_task_runtime import AgentTaskRuntime, TaskState
from artmach_assistant.core.agent_tool_session import AgentToolSession, AgentToolSessionError
from artmach_assistant.core.tool_registry import (
    PermissionLevel,
    ToolDefinition,
    ToolRegistry,
)


def _runtime() -> AgentTaskRuntime:
    registry = ToolRegistry()

    def read_value(context, value: int = 1):
        context.report_progress("okunuyor", 1, 2, None)
        return value

    def change_value(context, value: int = 1):
        context.report_progress("değiştiriliyor", 1, 1, None)
        return value * 2

    def slow(context):
        for index in range(20):
            context.raise_if_cancelled()
            context.report_progress("çalışıyor", index, 20, None)
            time.sleep(0.005)
        return "done"

    registry.register(ToolDefinition(
        name="read_value",
        description="read",
        permission=PermissionLevel.READ,
        handler=read_value,
    ))
    registry.register(ToolDefinition(
        name="change_value",
        description="change",
        permission=PermissionLevel.CHANGE,
        handler=change_value,
    ))
    registry.register(ToolDefinition(
        name="slow_task",
        description="slow",
        permission=PermissionLevel.READ,
        handler=slow,
    ))
    return AgentTaskRuntime(registry, max_workers=2)


def test_read_tool_runs_without_approval() -> None:
    runtime = _runtime()
    session = AgentToolSession(runtime)
    submitted = session.submit("read_value", {"value": 7})
    assert not submitted.approval_required
    finished = session.wait_latest(timeout=1)
    assert finished.state is TaskState.SUCCEEDED
    assert finished.result == 7
    runtime.close()


def test_change_tool_keeps_token_private_and_approves_latest() -> None:
    runtime = _runtime()
    session = AgentToolSession(runtime)
    submitted = session.submit(
        "change_value",
        {"value": 4},
        requested_permission=PermissionLevel.CHANGE,
    )
    assert submitted.approval_required
    assert "token" not in repr(submitted).lower()
    approved = session.approve_latest()
    assert approved.state in {TaskState.QUEUED, TaskState.RUNNING, TaskState.SUCCEEDED}
    finished = session.wait_latest(timeout=1)
    assert finished.result == 8
    runtime.close()


def test_cancel_latest_cancels_running_task() -> None:
    runtime = _runtime()
    session = AgentToolSession(runtime)
    session.submit("slow_task")
    time.sleep(0.02)
    cancelled = session.cancel_latest()
    assert cancelled.state in {TaskState.RUNNING, TaskState.CANCELLED}
    finished = session.wait_latest(timeout=1)
    assert finished.state is TaskState.CANCELLED
    runtime.close()


def test_status_without_task_is_clear_error() -> None:
    runtime = _runtime()
    session = AgentToolSession(runtime)
    with pytest.raises(AgentToolSessionError, match="araç görevi yok"):
        session.status_latest()
    runtime.close()


def test_clear_latest_removes_conversation_reference() -> None:
    runtime = _runtime()
    session = AgentToolSession(runtime)
    session.submit("read_value")
    assert session.clear_latest()
    assert not session.clear_latest()
    with pytest.raises(AgentToolSessionError):
        session.status_latest()
    runtime.close()
