from __future__ import annotations

from pathlib import Path

import pytest


APP_PATH = Path(__file__).resolve().parent / "app.py"


try:
    from artmach_assistant.app import BargeInWorker
except ImportError as exc:  # Linux CI image may not provide Qt's libEGL.
    pytest.skip(str(exc), allow_module_level=True)


class _FastStopVoice:
    def __init__(self) -> None:
        self.capture_kwargs: dict[str, object] = {}
        self.capture_device = None
        self.capture_calls = 0
        self.owner_threshold = 0.0

    def has_owner_voice_profile(self) -> bool:
        return True

    def record_utterance_wav(self, device_index, **kwargs) -> None:
        self.capture_calls += 1
        self.capture_device = device_index
        self.capture_kwargs = dict(kwargs)

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
    assert voice.capture_calls == 1
    assert voice.capture_kwargs["max_seconds"] == 1.20
    assert voice.capture_kwargs["wait_for_speech_seconds"] == 0.35
    assert voice.capture_kwargs["silence_stop_seconds"] == 0.30
    assert voice.capture_kwargs["min_capture_seconds"] == 0.30
    assert voice.capture_kwargs["wake_mode"] is False
    assert callable(voice.capture_kwargs["cancel_check"])
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

    assert voice.capture_calls == 1
    assert voice.owner_threshold == 0.73


class _NoWhisperVoice(_FastStopVoice):
    def listen_utterance(self, *_args, **_kwargs) -> str:
        raise AssertionError("barge-in capture must not invoke Whisper")

    def listen_for_local_stop(self, *_args, **_kwargs):
        raise AssertionError("capture-only barge-in must not use the old stop-profile path")


def test_barge_in_interrupts_without_whisper_or_stop_word_profile() -> None:
    voice = _NoWhisperVoice()
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
    assert voice.capture_calls == 1
    assert voice.owner_threshold == 0.73


def test_answer_tts_worker_is_bound_to_the_armed_speech_session() -> None:
    source = APP_PATH.read_text(encoding="utf-8")

    assert "speech_session_id = self.engine.voice.begin_speech_session()" in source
    assert "speech_session_id=speech_session_id" in source
