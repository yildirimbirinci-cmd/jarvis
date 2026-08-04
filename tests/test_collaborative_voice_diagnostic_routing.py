from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


class Store:
    def __init__(self) -> None:
        self.events = []

    def recent(self, *, limit=500, workspace=""):
        return tuple(self.events[-limit:])


def engine() -> AssistantEngine:
    value = AssistantEngine.__new__(AssistantEngine)
    value.runtime_events = Store()
    return value


def test_diagnostic_followup_starts_bounded_session() -> None:
    value = engine()

    answer = value._voice_diagnostic_request(
        "Ucuncu cozumle devam et. Kodu degistirmeden "
        "ses asamalarinin surelerini olc ve eski olaylari ayir."
    )

    assert answer is not None
    assert "VDG-" in answer
    assert hasattr(value, "_active_voice_diagnostic")


def test_diagnostic_completion_uses_only_new_events() -> None:
    value = engine()
    value._voice_diagnostic_request(
        "Kontrollu ses tanilamasini baslat. "
        "Kodu degistirmeden surelerini olc."
    )

    value.runtime_events.events.append(
        SimpleNamespace(
            event_id="fresh",
            component="VoiceService",
            action="stt_transcription",
            status="success",
            duration_ms=430.0,
            error_type="",
            message="",
        )
    )

    answer = value._voice_diagnostic_request(
        "Tanilama tamamlandi"
    )

    assert answer is not None
    assert "transkripsiyon" in answer
    assert "430 ms" in answer
    assert value._active_voice_diagnostic is None
