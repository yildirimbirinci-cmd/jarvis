from __future__ import annotations

from types import SimpleNamespace

import pytest

from artmach_assistant.core import ollama_changeset_model
from artmach_assistant.core.cancellable_ollama import OllamaChatResult
from artmach_assistant.core.ollama_changeset_model import OllamaChangesetModel


def _config(**overrides):
    values = {
        "ollama_url": "http://127.0.0.1:11434",
        "chat_model": "fast-chat",
        "code_model": "safe-coder",
        "code_context_window": 16384,
        "code_max_output_tokens": 4096,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_uses_configured_code_role_and_json_mode(monkeypatch) -> None:
    captured = {}

    def fake_chat(base_url, payload, **kwargs):
        captured["base_url"] = base_url
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return OllamaChatResult(
            content='{"operations": []}',
            done_reason="stop",
            total_bytes=32,
            chunks=1,
        )

    monkeypatch.setattr(ollama_changeset_model, "chat", fake_chat)
    model = OllamaChangesetModel(_config(), timeout_seconds=45)

    assert model.complete("make a changeset") == '{"operations": []}'
    assert model.model_name == "safe-coder"
    assert captured["base_url"] == "http://127.0.0.1:11434"
    assert captured["payload"]["model"] == "safe-coder"
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["options"] == {
        "temperature": 0.0,
        "num_ctx": 16384,
        "num_predict": 4096,
    }
    assert captured["kwargs"]["timeout"] == 45.0


def test_rejects_missing_ollama_url() -> None:
    with pytest.raises(ValueError, match="Ollama"):
        OllamaChangesetModel(_config(ollama_url=""))


def test_rejects_truncated_response(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_changeset_model,
        "chat",
        lambda *_args, **_kwargs: OllamaChatResult(
            content="{}",
            done_reason="length",
            total_bytes=2,
            chunks=1,
        ),
    )
    with pytest.raises(ValueError, match="truncated"):
        OllamaChangesetModel(_config()).complete("prompt")


def test_forwards_cancellation_callback(monkeypatch) -> None:
    cancelled = lambda: False
    captured = {}

    def fake_chat(*_args, **kwargs):
        captured.update(kwargs)
        return OllamaChatResult("{}", "stop", 2, 1)

    monkeypatch.setattr(ollama_changeset_model, "chat", fake_chat)
    OllamaChangesetModel(_config(), cancel_check=cancelled).complete("prompt")
    assert captured["cancel_check"] is cancelled
