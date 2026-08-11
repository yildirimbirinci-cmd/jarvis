from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine


def test_rejected_engineering_history_route_is_safe_fallthrough():
    engine = object.__new__(AssistantEngine)
    assert engine._rejected_engineering_history_request(
        "son tamamlanan engineering outcome dan ne ogrendin"
    ) is None


def test_stage7_persistent_handler_still_exists_after_compat_fix():
    assert callable(getattr(AssistantEngine, "_persistent_engineering_learning_request", None))
