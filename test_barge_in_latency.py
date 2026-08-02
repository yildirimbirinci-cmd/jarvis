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

    def has_stop_word_profile(self) -> bool:
        return False

    def listen_utterance(self, _device, seconds, _language, **kwargs) -> str:
        self.listen_seconds = float(seconds)
        self.listen_kwargs = dict(kwargs)
        return "dur"

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

    assert interruptions == ["learned:answer"]
    assert voice.listen_seconds == 1.20
    assert voice.listen_kwargs["model_size"] == "base"
    assert voice.listen_kwargs["wait_for_speech_seconds"] == 0.35
    assert voice.listen_kwargs["silence_stop_seconds"] == 0.30
    assert voice.listen_kwargs["min_capture_seconds"] == 0.30
    assert voice.listen_kwargs["wake_mode"] is False
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


class _EnrolledStopVoice(_FastStopVoice):
    def __init__(self) -> None:
        super().__init__()
        self.local_stop_calls = 0

    def has_stop_word_profile(self) -> bool:
        return True

    def listen_for_local_stop(self, _device, *, max_seconds, cancel_check):
        self.local_stop_calls += 1
        self.local_stop_seconds = max_seconds
        self.local_stop_cancel_check = cancel_check
        return True, 0.88

    def listen_utterance(self, *_args, **_kwargs) -> str:
        raise AssertionError("enrolled DUR must stop before Whisper")


def test_enrolled_dur_profile_interrupts_on_first_capture_without_whisper() -> None:
    voice = _EnrolledStopVoice()
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

    assert interruptions == ["profile:answer"]
    assert voice.local_stop_calls == 1
    assert voice.local_stop_seconds == 0.90
    assert voice.owner_threshold == 0.73


def test_answer_tts_worker_is_bound_to_the_armed_speech_session() -> None:
    source = APP_PATH.read_text(encoding="utf-8")

    assert "speech_session_id = self.engine.voice.begin_speech_session()" in source
    assert "speech_session_id=speech_session_id" in source
