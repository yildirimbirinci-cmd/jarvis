from __future__ import annotations

import threading

import pytest

from artmach_assistant.core.cancellable_ollama import OllamaChatResult
from artmach_assistant.core import local_dialogue as module
from artmach_assistant.core.local_dialogue import LocalDialogueManager


class _Lab:
    def __init__(self) -> None:
        self.rows: list[tuple[str, int, bool]] = []

    def record(self, kind: str, elapsed: int, ok: bool) -> None:
        self.rows.append((kind, elapsed, ok))


def _manager() -> LocalDialogueManager:
    manager = LocalDialogueManager.__new__(LocalDialogueManager)
    manager.model = "local-chat"
    manager.url = "http://127.0.0.1:11434"
    manager._history_lock = threading.RLock()
    manager.history = []
    manager.lab = _Lab()
    return manager


def test_interpret_forwards_cancellation_and_progress(monkeypatch) -> None:
    manager = _manager()
    marker = object()
    progress = []
    captured = {}

    def fake_chat(*args, **kwargs):
        captured.update(kwargs)
        kwargs["progress_callback"](12)
        return OllamaChatResult(
            content='{"kind":"chat","response":"Merhaba","confidence":0.9}',
            done_reason="stop",
            total_bytes=10,
            chunks=1,
        )

    monkeypatch.setattr(module, "ollama_chat", fake_chat)
    decision = manager.interpret(
        "merhaba",
        False,
        cancel_check=lambda: marker is None,
        progress_callback=progress.append,
    )

    assert decision is not None
    assert decision.response == "Merhaba"
    assert callable(captured["cancel_check"])
    assert progress == [12]
    assert manager.lab.rows[-1][2] is True


def test_respond_propagates_interrupted_error(monkeypatch) -> None:
    manager = _manager()

    def cancelled(*_args, **_kwargs):
        raise InterruptedError("cancelled")

    monkeypatch.setattr(module, "ollama_chat", cancelled)
    with pytest.raises(InterruptedError, match="cancelled"):
        manager.respond("uzun soru", cancel_check=lambda: True)
    assert manager.lab.rows[-1][0] == "yanıt"
    assert manager.lab.rows[-1][2] is False


def test_respond_rejects_length_truncated_answer(monkeypatch) -> None:
    manager = _manager()
    monkeypatch.setattr(
        module,
        "ollama_chat",
        lambda *_args, **_kwargs: OllamaChatResult(
            content="yarım cevap",
            done_reason="length",
            total_bytes=20,
            chunks=2,
        ),
    )

    answer = manager.respond("soru")

    assert answer is not None
    assert "tamamlayamadım" in answer
    assert manager.lab.rows[-1][2] is False
