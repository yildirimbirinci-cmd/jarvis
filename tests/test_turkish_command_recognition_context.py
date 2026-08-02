from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.voice_service import (
    VoiceService,
    _repair_turkish_command_text,
)


def test_turkish_command_uses_jarvis_source_code_context(
    tmp_path: Path, monkeypatch
) -> None:
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"audio")
    observed = {}

    class _Model:
        def transcribe(self, path, **options):
            observed.update(options)
            return (
                [
                    SimpleNamespace(
                        text=" Kendi kodlarını incele.",
                        no_speech_prob=0.01,
                        avg_logprob=-0.2,
                    )
                ],
                SimpleNamespace(language_probability=0.99),
            )

    service = VoiceService()
    monkeypatch.setattr(service, "_non_speech_signal_reason", lambda _path: None)
    monkeypatch.setattr(service, "_whisper_model", lambda _size: _Model())

    text = service.recognize_wav(
        audio, language="tr-TR", model_size="small", wake_mode=False
    )

    assert text == "Kendi kodlarını incele."
    assert observed["language"] == "tr"
    assert "kendi kodlarını incele" in observed["initial_prompt"]
    assert "geliştirilmesi gereken yerleri" in observed["initial_prompt"]


def test_wake_recognition_does_not_use_long_command_context(
    tmp_path: Path, monkeypatch
) -> None:
    audio = tmp_path / "wake.wav"
    audio.write_bytes(b"audio")
    observed = {}

    class _Model:
        def transcribe(self, path, **options):
            observed.update(options)
            return (
                [
                    SimpleNamespace(
                        text=" Jarvis",
                        no_speech_prob=0.01,
                        avg_logprob=-0.2,
                    )
                ],
                SimpleNamespace(language_probability=0.99),
            )

    service = VoiceService()
    monkeypatch.setattr(service, "_non_speech_signal_reason", lambda _path: None)
    monkeypatch.setattr(service, "_whisper_model", lambda _size: _Model())

    service.recognize_wav(
        audio,
        language="tr-TR",
        model_size="base",
        wake_mode=True,
        hotwords="jarvis cervis",
    )

    assert "initial_prompt" not in observed
    assert observed["hotwords"] == "jarvis cervis"


def test_observed_turkish_command_confusions_are_repaired() -> None:
    assert (
        _repair_turkish_command_text("Bana özelliklerimi at.")
        == "Bana özelliklerimi anlat."
    )
    assert (
        _repair_turkish_command_text(
            "Koşma özelliklerinin ilgili kodlar görebiliyorsun."
        ).startswith("konuşma özellik")
    )
    assert (
        _repair_turkish_command_text("Kodlarını güzellebiliyorsun.")
        == "kodlarını düzenleyebiliyor musun."
    )
