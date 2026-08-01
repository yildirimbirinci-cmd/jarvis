from __future__ import annotations

import time

from artmach_assistant.core.agent_task_runtime import AgentTaskRuntime, TaskState
from artmach_assistant.core.agent_tool_command_bridge import AgentToolCommandBridge
from artmach_assistant.core.agent_tool_session import AgentToolSession
from artmach_assistant.core.tool_registry import PermissionLevel, ToolDefinition, ToolRegistry


def _build():
    registry = ToolRegistry()

    def read(context):
        context.report_progress("okunuyor", 1, 1, None)
        return "ok"

    def change(context):
        context.report_progress("değiştiriliyor", 1, 1, None)
        return "changed"

    def slow(context):
        for index in range(50):
            context.raise_if_cancelled()
            context.report_progress("çalışıyor", index, 50, None)
            time.sleep(0.002)
        return "done"

    registry.register(ToolDefinition("read_value", "Okur.", PermissionLevel.READ, read))
    registry.register(ToolDefinition("change_value", "Değiştirir.", PermissionLevel.CHANGE, change))
    registry.register(ToolDefinition("slow_value", "Yavaş çalışır.", PermissionLevel.READ, slow))
    runtime = AgentTaskRuntime(registry, max_workers=2)
    session = AgentToolSession(runtime)
    bridge = AgentToolCommandBridge(session, registry)
    return registry, runtime, session, bridge


def test_lists_registered_tools_with_permissions() -> None:
    _registry, runtime, _session, bridge = _build()
    result = bridge.handle("araçları göster")
    assert result.handled
    assert "read_value [okuma]" in result.response
    assert "change_value [değişiklik]" in result.response
    runtime.close()


def test_approve_command_consumes_private_token() -> None:
    _registry, runtime, session, bridge = _build()
    view = session.submit("change_value", requested_permission=PermissionLevel.CHANGE)
    assert view.approval_required
    result = bridge.handle("araç işlemini onayla")
    assert result.handled
    finished = session.wait_latest(timeout=1)
    assert finished.state is TaskState.SUCCEEDED
    assert finished.result == "changed"
    runtime.close()


def test_status_and_cancel_are_conversational() -> None:
    _registry, runtime, session, bridge = _build()
    session.submit("slow_value")
    status = bridge.handle("işlem ne durumda")
    assert status.handled
    cancelled = bridge.handle("araç işlemini iptal et")
    assert cancelled.handled
    finished = session.wait_latest(timeout=1)
    assert finished.state is TaskState.CANCELLED
    runtime.close()


def test_missing_task_returns_clear_message() -> None:
    _registry, runtime, _session, bridge = _build()
    result = bridge.handle("işlemi onayla")
    assert result.handled
    assert "Takip edilen bir araç görevi yok" in result.response
    runtime.close()
