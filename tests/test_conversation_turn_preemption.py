from __future__ import annotations

import pytest

from artmach_assistant.core import conversation_runtime as runtime_module
from artmach_assistant.core.conversation_runtime import (
    ConversationPhase,
    ConversationRuntime,
    StaleConversationTurnError,
)


class _ExternalToken:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def cancel(self, reason: str = "") -> None:
        self.calls.append(reason)


def test_new_turn_cancels_previous_token_and_speech_once() -> None:
    runtime = ConversationRuntime()
    first = runtime.begin_turn("ilk")
    token = runtime.token_for(first)
    assert token is not None
    speech_reasons: list[str] = []
    runtime.response_ready("yanıt", "yanıt", turn_id=first)
    assert runtime.mark_speaking(
        turn_id=first,
        cancel_callback=speech_reasons.append,
    )

    second = runtime.begin_turn("ikinci")

    assert second != first
    assert token.cancelled is True
    assert token.reason == "yeni konuşma turu başladı"
    assert speech_reasons == ["yeni konuşma turu başladı"]
    assert runtime.phase == ConversationPhase.THINKING


def test_stale_or_cancelled_turn_cannot_publish_response() -> None:
    runtime = ConversationRuntime()
    first = runtime.begin_turn("ilk")
    runtime.begin_turn("ikinci")
    with pytest.raises(StaleConversationTurnError):
        runtime.response_ready("eski", "eski", turn_id=first)

    current = runtime.current_turn_id
    assert runtime.cancel("dur", turn_id=current)
    with pytest.raises(InterruptedError, match="dur"):
        runtime.response_ready("geç", "geç", turn_id=current)


def test_late_legacy_tts_completion_cannot_overwrite_new_thinking_turn() -> None:
    runtime = ConversationRuntime()
    first = runtime.begin_turn("ilk")
    runtime.response_ready("yanıt", "yanıt", turn_id=first)
    runtime.mark_speaking(turn_id=first)
    runtime.begin_turn("yeni cümle")

    assert runtime.complete("eski tts bitti") is False
    assert runtime.phase == ConversationPhase.THINKING
    assert runtime.snapshot().request == "yeni cümle"


def test_turn_cancellation_is_forwarded_to_linked_task() -> None:
    runtime = ConversationRuntime()
    turn = runtime.begin_turn("uzun görev")
    external = _ExternalToken()
    assert runtime.begin_task("test", turn_id=turn, cancellation=external)

    assert runtime.cancel("kullanıcı susturdu", turn_id=turn)
    assert external.calls == ["kullanıcı susturdu"]


def test_dialogue_lease_expires_and_can_be_closed(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(runtime_module.time, "monotonic", lambda: clock[0])
    runtime = ConversationRuntime()
    runtime.open_dialogue(5)
    assert runtime.dialogue_open() is True
    clock[0] = 106.0
    assert runtime.dialogue_open() is False
    runtime.open_dialogue(20)
    runtime.close_dialogue()
    assert runtime.dialogue_open() is False
