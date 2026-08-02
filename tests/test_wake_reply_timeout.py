from __future__ import annotations

import threading
import time

from artmach_assistant.app import WakeWordWorker


class HangingVoice:
    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.stop_calls = 0

    def speak(self, *_args, **_kwargs) -> None:
        self.cancelled.wait(10.0)

    def stop_speaking(self) -> bool:
        self.stop_calls += 1
        self.cancelled.set()
        return True


class PreparingVoice:
    def __init__(self) -> None:
        self.prepared: list[tuple[str, tuple[object, ...]]] = []

    def prepare_speech(self, text, *args) -> None:
        self.prepared.append((text, args))


def make_worker(voice) -> WakeWordWorker:
    return WakeWordWorker(
        voice,
        None,
        "tr",
        "jarvis",
        "base",
        "small",
        1.5,
        20.0,
        (),
    )


def test_wake_reply_timeout_cancels_stalled_audio(monkeypatch) -> None:
    voice = HangingVoice()
    worker = make_worker(voice)
    messages: list[str] = []
    worker.status.connect(messages.append)

    original_wait = threading.Event.wait

    def short_wait(event, timeout=None):
        if event is voice.cancelled:
            return original_wait(event, timeout)
        return original_wait(event, min(float(timeout or 0), 0.02))

    monkeypatch.setattr(threading.Event, "wait", short_wait)
    started = time.monotonic()
    worker._speak_wake_reply("Evet.", [])

    assert time.monotonic() - started < 0.5
    assert voice.stop_calls == 1
    assert any("zaman aşımına uğradı" in message for message in messages)


def test_default_wake_reply_is_prepared_with_partial_custom_responses(
    monkeypatch,
) -> None:
    voice = PreparingVoice()
    worker = WakeWordWorker(
        voice,
        None,
        "tr",
        "jarvis",
        "base",
        "small",
        1.5,
        20.0,
        ("", 0, 100, "piper", "piper.exe", "voice.onnx", 4),
        wake_responses={"asistan": "Efendim."},
    )
    # QThread clears a pre-start interruption request when run begins.  Stub
    # the query itself so this unit test verifies preparation without ever
    # entering the real microphone/wake loop.
    monkeypatch.setattr(worker, "isInterruptionRequested", lambda: True)

    worker.run()

    assert {text for text, _args in voice.prepared} == {"Evet.", "Efendim."}
