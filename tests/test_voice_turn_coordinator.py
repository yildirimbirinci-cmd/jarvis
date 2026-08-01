from __future__ import annotations

from artmach_assistant.core.conversation_runtime import (
    ConversationPhase,
    ConversationRuntime,
)
from artmach_assistant.core.task_orchestrator import TaskOrchestrator
from artmach_assistant.core.voice_turn_coordinator import VoiceTurnCoordinator


class _Voice:
    def __init__(self) -> None:
        self.counter = 0
        self.active = ""
        self.stop_calls: list[str | None] = []

    def begin_speech_session(self) -> str:
        self.counter += 1
        self.active = f"speech-{self.counter}"
        return self.active

    def stop_speaking(self, session_id: str | None = None) -> bool:
        self.stop_calls.append(session_id)
        if session_id and session_id == self.active:
            self.active = ""
            return True
        return False


def _coordinator(tmp_path):
    runtime = ConversationRuntime()
    orchestrator = TaskOrchestrator(
        history_file=tmp_path / "history.json",
        active_file=tmp_path / "active.json",
    )
    voice = _Voice()
    return VoiceTurnCoordinator(runtime, orchestrator, voice), runtime, orchestrator, voice


def test_turn_task_and_speech_share_one_identity(tmp_path) -> None:
    coordinator, runtime, orchestrator, _voice = _coordinator(tmp_path)
    turn_id = coordinator.begin_turn("uzun bir görev")
    record, token = coordinator.start_task("Kod analizi", "voice", turn_id)
    runtime.response_ready("yanıt", "yanıt", turn_id=turn_id)
    speech_id = coordinator.begin_speech(turn_id)

    assert coordinator.binding.turn_id == turn_id
    assert coordinator.binding.task_id == record.task_id
    assert coordinator.binding.speech_session_id == speech_id
    assert record.turn_id == turn_id
    assert token.cancelled is False
    assert orchestrator.active is not None
    assert runtime.phase == ConversationPhase.SPEAKING


def test_preempt_cancels_turn_task_and_only_scoped_speech(tmp_path) -> None:
    coordinator, runtime, orchestrator, voice = _coordinator(tmp_path)
    turn_id = coordinator.begin_turn("ilk soru")
    turn_token = runtime.token_for(turn_id)
    record, task_token = coordinator.start_task("Model", "voice", turn_id)
    runtime.response_ready("ilk cevap", "ilk cevap", turn_id=turn_id)
    speech_id = coordinator.begin_speech(turn_id)

    assert coordinator.preempt(
        "sahibin yeni cümlesi",
        pending_command="ikinci soru",
        pending_source="voice",
    )

    assert turn_token is not None and turn_token.cancelled is True
    assert task_token.cancelled is True
    assert orchestrator.active is not None
    assert orchestrator.active.state == "cancelling"
    assert speech_id in [row for row in voice.stop_calls if row]
    assert None not in voice.stop_calls
    pending = coordinator.take_pending_request()
    assert pending is not None
    assert pending.command == "ikinci soru"
    assert pending.source == "voice"

    finished = orchestrator.finish(record.task_id, cancelled=True)
    assert finished is not None
    assert finished.state == "cancelled"


def test_latest_pending_owner_request_wins(tmp_path) -> None:
    coordinator, _runtime, _orchestrator, _voice = _coordinator(tmp_path)

    assert coordinator.queue_command("eski istek", source="voice")
    assert coordinator.queue_command("en yeni istek", source="keyboard")

    request = coordinator.take_pending_request()
    assert request is not None
    assert request.command == "en yeni istek"
    assert request.source == "keyboard"
    assert coordinator.has_pending_command() is False


def test_stale_tts_callback_cannot_complete_new_turn(tmp_path) -> None:
    coordinator, runtime, _orchestrator, _voice = _coordinator(tmp_path)
    first = coordinator.begin_turn("ilk")
    runtime.response_ready("ilk yanıt", "ilk yanıt", turn_id=first)
    first_session = coordinator.begin_speech(first)

    second = coordinator.begin_turn("ikinci")
    assert second != first
    assert coordinator.complete_speech(first, first_session, "eski TTS") is False
    assert runtime.phase == ConversationPhase.THINKING
    assert runtime.snapshot().request == "ikinci"

    runtime.response_ready("ikinci yanıt", "ikinci yanıt", turn_id=second)
    second_session = coordinator.begin_speech(second)
    assert coordinator.complete_speech(second, second_session, "yeni TTS") is True
    assert runtime.phase == ConversationPhase.COMPLETED


def test_preempt_without_speech_never_uses_unscoped_stop(tmp_path) -> None:
    coordinator, runtime, _orchestrator, voice = _coordinator(tmp_path)
    turn_id = coordinator.begin_turn("yalnızca düşün")

    assert coordinator.preempt("dur") is True
    assert runtime.token_for(turn_id) is not None
    assert runtime.token_for(turn_id).cancelled is True
    assert voice.stop_calls == []
