from pathlib import Path

from artmach_assistant.core.runtime_instrumentation import (
    _callable_identity_metadata,
)


def test_explicit_action_identity_wins_over_lambda_source(tmp_path) -> None:
    action = lambda: None
    action.__jarvis_action_module__ = "artmach_assistant.core.assistant"
    action.__jarvis_action_path__ = "core/assistant.py"
    action.__jarvis_action_symbol__ = "AssistantEngine.handle"

    metadata = _callable_identity_metadata(action, tmp_path)

    assert metadata["action_module"] == "artmach_assistant.core.assistant"
    assert metadata["action_path"] == "core/assistant.py"
    assert metadata["action_symbol"] == "AssistantEngine.handle"


def test_gui_conversation_action_is_explicitly_tagged() -> None:
    source = Path("core/gui_voice_integration.py").read_text(encoding="utf-8")
    assert 'action.__jarvis_action_path__ = "core/assistant.py"' in source
    assert 'action.__jarvis_action_symbol__ = "AssistantEngine.handle"' in source
