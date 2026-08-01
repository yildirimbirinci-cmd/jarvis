import threading

import pytest

from artmach_assistant.core.tool_registry import (
    PermissionLevel,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolRegistryError,
)


def context() -> ToolContext:
    return ToolContext(
        task_id="task",
        operation_id="operation",
        cancel_event=threading.Event(),
        report_progress=lambda *args: None,
    )


def test_register_list_and_invoke_read_tool():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="echo_text",
        description="Echo text",
        permission=PermissionLevel.READ,
        handler=lambda ctx, text: text,
    ))
    assert registry.invoke(
        "echo_text", context(), {"text": "merhaba"},
        granted_permission=PermissionLevel.READ,
    ) == "merhaba"
    assert [item.name for item in registry.list_tools()] == ["echo_text"]


def test_duplicate_and_invalid_names_are_rejected():
    registry = ToolRegistry()
    definition = ToolDefinition("valid_tool", "x", PermissionLevel.READ, lambda ctx: None)
    registry.register(definition)
    with pytest.raises(ToolRegistryError, match="zaten"):
        registry.register(definition)
    with pytest.raises(ToolRegistryError, match="Araç adı"):
        registry.register(ToolDefinition("Bad Tool", "x", PermissionLevel.READ, lambda ctx: None))


def test_permission_is_enforced():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        "write_file", "write", PermissionLevel.CHANGE, lambda ctx: "done"
    ))
    with pytest.raises(ToolRegistryError, match="CHANGE"):
        registry.invoke("write_file", context(), granted_permission=PermissionLevel.READ)


def test_argument_validation_happens_before_handler():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        "needs_value", "x", PermissionLevel.READ, lambda ctx, value: value
    ))
    with pytest.raises(ToolRegistryError, match="parametreleri"):
        registry.invoke("needs_value", context(), {}, granted_permission=PermissionLevel.READ)


def test_cancelled_context_prevents_invocation():
    registry = ToolRegistry()
    called = False

    def handler(ctx):
        nonlocal called
        called = True

    registry.register(ToolDefinition("never_runs", "x", PermissionLevel.READ, handler))
    ctx = context()
    ctx.cancel_event.set()
    with pytest.raises(ToolRegistryError, match="iptal"):
        registry.invoke("never_runs", ctx, granted_permission=PermissionLevel.READ)
    assert not called
