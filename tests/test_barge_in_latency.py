from __future__ import annotations

from pathlib import Path

import pytest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


try:
    from artmach_assistant.app import BargeInWorker
except ImportError as exc:  # Linux CI image may not provide Qt's libEGL.
    pytest.skip(str(exc), allow_module_level=True)


class _FastStopVoice:
    def __init__(self) -> None:
        self.listen_kwargs: dict[str, object] = {}
        self.listen_seconds = 0.0

    def has_owner_voice_profile(self) -> bool:
        return True

    def record_utterance_wav(self, _device, *, max_seconds, **kwargs):
        self.listen_seconds = float(max_seconds)
        self.listen_kwargs = dict(kwargs)
        return Path("barge-in.wav")

    def verify_owner_voice(self, *, threshold: float):
        self.owner_threshold = threshold
        return True, 0.99


def test_barge_in_uses_short_dedicated_capture_profile() -> None:
    voice = _FastStopVoice()
    worker = BargeInWorker(
        voice,
        None,
        0.82,
        ["dur", "sus", "kes", "iptal"],
        "answer",
    )
    interruptions: list[str] = []
    worker.interrupted.connect(interruptions.append)

    worker.run()

    assert interruptions == ["owner:answer"]
    assert voice.listen_seconds == 1.20
    assert voice.listen_kwargs["wait_for_speech_seconds"] == 0.35
    assert voice.listen_kwargs["silence_stop_seconds"] == 0.30
    assert voice.listen_kwargs["min_capture_seconds"] == 0.30
    assert "wake_mode" not in voice.listen_kwargs
    assert voice.owner_threshold == 0.82


def test_barge_in_preserves_calibrated_owner_threshold_below_082() -> None:
    voice = _FastStopVoice()
    worker = BargeInWorker(
        voice,
        None,
        0.73,
        ["dur", "sus", "kes", "iptal"],
        "answer",
    )

    worker.run()

    assert voice.owner_threshold == 0.73


def test_owner_barge_in_needs_neither_stop_profile_nor_whisper() -> None:
    voice = _FastStopVoice()
    voice.has_stop_word_profile = lambda: (_ for _ in ()).throw(
        AssertionError("barge-in must not require a DUR profile")
    )
    voice.listen_utterance = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("barge-in must not wait for Whisper")
    )
    worker = BargeInWorker(
        voice,
        None,
        0.73,
        ["dur", "sus", "kes", "iptal"],
        "answer",
    )
    interruptions: list[str] = []
    worker.interrupted.connect(interruptions.append)

    worker.run()

    assert interruptions == ["owner:answer"]
    assert voice.listen_seconds == 1.20
    assert voice.owner_threshold == 0.73


def test_answer_tts_worker_is_bound_to_the_armed_speech_session() -> None:
    source = APP_PATH.read_text(encoding="utf-8")

    assert "speech_session_id = self.engine.voice.begin_speech_session()" in source
    assert "speech_session_id=speech_session_id" in source

class _SilenceThenOwnerVoice(_FastStopVoice):
    def __init__(self) -> None:
        super().__init__()
        self.capture_calls = 0

    def record_utterance_wav(self, _device, *, max_seconds, **kwargs):
        self.capture_calls += 1
        self.listen_seconds = float(max_seconds)
        self.listen_kwargs = dict(kwargs)
        if self.capture_calls <= 2:
            raise RuntimeError(
                "Konuşma algılanamadı; wake word bekleme süresi doldu."
            )
        return Path("barge-in.wav")


def test_barge_in_treats_silence_as_idle_not_device_failure() -> None:
    voice = _SilenceThenOwnerVoice()
    worker = BargeInWorker(
        voice,
        None,
        0.73,
        ["dur"],
        "answer",
    )
    statuses: list[str] = []
    interruptions: list[str] = []
    worker.status.connect(statuses.append)
    worker.interrupted.connect(interruptions.append)

    worker.run()

    assert voice.capture_calls == 3
    assert interruptions == ["owner:answer"]
    assert not any("üç" in row.casefold() and "hata" in row.casefold() for row in statuses)
    assert not any("kesme sesi alınamadı" in row.casefold() for row in statuses)
