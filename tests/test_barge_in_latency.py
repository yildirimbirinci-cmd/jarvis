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

    def listen_utterance(self, _device, seconds, _language, **kwargs) -> str:
        self.listen_seconds = float(seconds)
        self.listen_kwargs = dict(kwargs)
        return "dur"

    def verify_owner_voice(self, *, threshold: float):
        assert threshold >= 0.82
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
    assert voice.listen_kwargs["wake_mode"] is False


def test_answer_tts_worker_is_bound_to_the_armed_speech_session() -> None:
    source = APP_PATH.read_text(encoding="utf-8")

    assert "speech_session_id = self.engine.voice.begin_speech_session()" in source
    assert "speech_session_id=speech_session_id" in source
