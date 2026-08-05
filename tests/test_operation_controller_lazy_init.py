from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.operation_control import OperationController


def test_operation_controller_is_created_lazily() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)

    assert not hasattr(engine, "operation_controller")
    assert engine.operation_status_report() == "Şu anda çalışan uzun bir işlem yok."
    assert isinstance(engine.operation_controller, OperationController)


def test_cancel_without_initialized_controller_is_safe() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)

    rendered = engine.cancel_active_operation()

    assert "iptal edilebilecek" in rendered
    assert isinstance(engine.operation_controller, OperationController)
