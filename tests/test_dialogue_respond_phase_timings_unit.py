from types import SimpleNamespace
import artmach_assistant.core.local_dialogue as module
from artmach_assistant.core.local_dialogue import LocalDialogueManager

class Lab:
    def record(self, *args, **kwargs):
        pass

def test_respond_exposes_context_model_and_output_timings(monkeypatch):
    manager = object.__new__(LocalDialogueManager)
    manager.model = "test"
    manager.url = "http://localhost"
    manager.lab = Lab()
    manager.context_window = 4096
    manager.max_output_tokens = 128
    manager._data_message = lambda *args, **kwargs: None
    manager._prompt_context_budget = lambda **kwargs: 1000
    manager._context_messages = lambda *args, **kwargs: []
    manager._context_window_limit = lambda: 4096
    manager._output_token_limit = lambda: 128
    monkeypatch.setattr(
        module,
        "ollama_chat",
        lambda *args, **kwargs: SimpleNamespace(truncated=False, content="ok"),
    )
    result = manager.respond("merhaba")
    assert result == "ok"
    assert set(manager.last_respond_timings) == {
        "context_prepare", "ollama_chat", "output_process"
    }
    assert all(value >= 0 for value in manager.last_respond_timings.values())
