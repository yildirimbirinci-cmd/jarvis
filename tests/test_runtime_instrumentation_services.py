from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_instrumentation(monkeypatch):
    instrumentation = importlib.import_module("artmach_assistant.core.runtime_instrumentation")
    instrumentation.reset_runtime_instrumentation_for_tests()
    yield
    instrumentation.reset_runtime_instrumentation_for_tests()


def _runtime_types():
    instrumentation = importlib.import_module("artmach_assistant.core.runtime_instrumentation")
    voice_module = importlib.import_module("artmach_assistant.core.voice_service")
    dialogue_module = importlib.import_module("artmach_assistant.core.local_dialogue")
    return instrumentation, voice_module.VoiceService, dialogue_module.LocalDialogueManager


def test_voice_and_dialogue_entry_points_emit_sanitized_events(monkeypatch, tmp_path: Path) -> None:
    instrumentation, VoiceService, LocalDialogueManager = _runtime_types()
    events: list[dict[str, object]] = []

    def recorder(**payload):
        events.append(payload)
        return True

    audio = tmp_path / "utterance.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 128)

    monkeypatch.setattr(VoiceService, "record_utterance_wav", lambda self, *a, **k: audio)
    monkeypatch.setattr(VoiceService, "listen_utterance", lambda self, *a, **k: "merhaba jarvis")
    monkeypatch.setattr(VoiceService, "listen_for_local_stop", lambda self, *a, **k: (False, 0.2))
    monkeypatch.setattr(VoiceService, "stop_speaking", lambda self: None)
    monkeypatch.setattr(
        VoiceService,
        "speak",
        lambda self, *a, **k: "Windows TTS kullanıldı: Ayşe. Piper hatası: Invalid sample rate -9997",
    )
    monkeypatch.setattr(
        LocalDialogueManager,
        "health",
        lambda self: (False, "Yerel model servisine ulaşılamadı."),
    )
    monkeypatch.setattr(LocalDialogueManager, "respond", lambda self, *a, **k: None)

    instrumentation.configure_runtime_instrumentation(recorder, workspace_provider=lambda: tmp_path)
    installed = instrumentation.install_runtime_instrumentation()
    assert installed >= 10

    voice = VoiceService()
    assert voice.record_utterance_wav(None, 2.0) == audio
    assert voice.listen_utterance(None, 3.0, model_size="small") == "merhaba jarvis"
    assert voice.listen_for_local_stop(None) == (False, 0.2)
    voice.stop_speaking()
    assert "Windows TTS" in voice.speak("gizli kullanıcı cümlesi", backend="auto")

    dialogue = LocalDialogueManager("chat-model", "http://127.0.0.1:11434")
    assert dialogue.health()[0] is False
    assert dialogue.respond("özel kullanıcı sorusu") is None

    by_action = {str(event["action"]): event for event in events}
    assert by_action["audio_capture"]["status"] == "completed"
    assert by_action["audio_capture"]["metadata"]["captured_bytes"] == audio.stat().st_size
    assert by_action["speech_turn"]["metadata"]["transcript_chars"] == len("merhaba jarvis")
    assert "stop_candidate" not in by_action
    assert by_action["tts_interrupt"]["status"] == "completed"
    assert by_action["tts_dispatch"]["status"] == "warning"
    assert by_action["tts_dispatch"]["metadata"]["piper_error"] is True
    assert by_action["model_health"]["status"] == "failed"
    assert by_action["model_health"]["metadata"]["model"] == "chat-model"
    assert by_action["chat_model"]["status"] == "warning"

    # Raw user text is never copied into metadata.
    serialized = repr(events)
    assert "gizli kullanıcı cümlesi" not in serialized
    assert "özel kullanıcı sorusu" not in serialized
    assert all(event.get("correlation_id") for event in events)
    assert any(name.endswith("VoiceService.speak") for name in instrumentation.runtime_instrumentation_coverage())


def test_expected_no_speech_is_not_counted_as_runtime_failure(monkeypatch, tmp_path: Path) -> None:
    instrumentation, VoiceService, _ = _runtime_types()
    events: list[dict[str, object]] = []

    def recorder(**payload):
        events.append(payload)
        return True

    def no_speech(self, *args, **kwargs):
        raise RuntimeError("Konuşma algılanamadı; ses sinyali yok.")

    monkeypatch.setattr(VoiceService, "recognize_wav", no_speech)
    instrumentation.configure_runtime_instrumentation(recorder, workspace_provider=lambda: tmp_path)
    instrumentation.install_runtime_instrumentation()

    with pytest.raises(RuntimeError, match="Konuşma algılanamadı"):
        VoiceService().recognize_wav(tmp_path / "missing.wav")

    # Ordinary silence is expected in continuous listening and must not evict
    # real failures from the bounded event store.
    assert not any(item["action"] == "stt_transcription" for item in events)
