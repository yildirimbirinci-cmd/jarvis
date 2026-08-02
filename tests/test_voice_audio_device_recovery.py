from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from artmach_assistant.core.audio_device_resilience import AudioRouteStore
from artmach_assistant.core.voice_service import VoiceService


class _InputStream:
    def __init__(self, *, channels: int, **_kwargs) -> None:
        self.channels = channels

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, frames: int):
        time.sleep(0.020)
        return np.zeros((frames, self.channels), dtype=np.int16), False


class _OutputStream:
    def __init__(self, owner, **kwargs) -> None:
        self.owner = owner
        self.kwargs = kwargs
        self.active = False
        self.writes = 0
        owner.streams.append(self)

    def start(self) -> None:
        self.active = True

    def write(self, block) -> None:
        self.writes += 1
        if self.owner.fail_after_first_write and self.writes >= 2:
            raise RuntimeError("Invalid device -9996")
        self.owner.frames_written += int(block.shape[0])

    def stop(self) -> None:
        self.active = False

    def abort(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False


class _SoundDevice:
    def __init__(self, *, fail_after_first_write: bool = False) -> None:
        self.default = SimpleNamespace(device=(0, 3))
        self.fail_after_first_write = fail_after_first_write
        self.frames_written = 0
        self.streams: list[_OutputStream] = []
        self.checked_input: list[tuple[int, int]] = []
        self.checked_output: list[tuple[int, int]] = []
        self._hostapis = [
            {"name": "Windows WASAPI"},
            {"name": "MME"},
            {"name": "Windows WDM-KS"},
        ]
        self._devices = [
            {
                "name": "Realtek Microphone",
                "hostapi": 0,
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 48000,
            },
            {
                "name": "Realtek Line In",
                "hostapi": 0,
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 48000,
            },
            {
                "name": "Logitech G635 Microphone",
                "hostapi": 0,
                "max_input_channels": 1,
                "max_output_channels": 0,
                "default_samplerate": 48000,
            },
            {
                "name": "Realtek Speakers",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000,
            },
            {
                "name": "Logitech G635 Speakers",
                "hostapi": 0,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000,
            },
            {
                "name": "Logitech G635 Speakers",
                "hostapi": 1,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 44100,
            },
            {
                "name": "Unsafe Kernel Endpoint",
                "hostapi": 2,
                "max_input_channels": 1,
                "max_output_channels": 2,
                "default_samplerate": 48000,
            },
        ]

    def query_devices(self, device=None, kind=None):
        if device is None and kind is None:
            return list(self._devices)
        if device is None and kind == "input":
            device = self.default.device[0]
        elif device is None and kind == "output":
            device = self.default.device[1]
        return dict(self._devices[int(device)])

    def query_hostapis(self):
        return list(self._hostapis)

    def check_input_settings(self, *, device, samplerate, **_kwargs) -> None:
        self.checked_input.append((int(device), int(samplerate)))

    def InputStream(self, **kwargs):
        return _InputStream(**kwargs)

    def check_output_settings(self, *, device, samplerate, **_kwargs) -> None:
        self.checked_output.append((int(device), int(samplerate)))
        if int(device) == 4 and int(samplerate) == 24000:
            raise RuntimeError("Invalid sample rate -9997")

    def OutputStream(self, **kwargs):
        return _OutputStream(self, **kwargs)


def _service(monkeypatch, tmp_path, *, partial_failure: bool = False):
    service = VoiceService()
    fake = _SoundDevice(fail_after_first_write=partial_failure)
    monkeypatch.setattr(service, "_sounddevice", lambda: fake)
    service._audio_routes = AudioRouteStore(tmp_path / "routes.json")
    return service, fake


def test_cancelled_session_cannot_start_after_output_stream_open_unblocks(
    monkeypatch,
    tmp_path,
) -> None:
    service, fake = _service(monkeypatch, tmp_path)
    opening = threading.Event()
    release = threading.Event()
    original_output_stream = fake.OutputStream

    def blocked_output_stream(**kwargs):
        opening.set()
        assert release.wait(2.0)
        return original_output_stream(**kwargs)

    monkeypatch.setattr(fake, "OutputStream", blocked_output_stream)
    session_id = service.begin_speech_session()
    cancel_event = service._speech_cancel_event
    result: dict[str, object] = {}

    def play() -> None:
        try:
            service._play_audio_resilient(
                np.ones((2400,), dtype=np.float32),
                24000,
                3,
                session_id=session_id,
                cancel_event=cancel_event,
            )
        except BaseException as exc:
            result["error"] = exc

    worker = threading.Thread(target=play, daemon=True)
    worker.start()
    assert opening.wait(1.0)
    assert service.stop_speaking(session_id) is True
    release.set()
    worker.join(timeout=2.0)

    assert worker.is_alive() is False
    assert isinstance(result.get("error"), InterruptedError)
    assert fake.frames_written == 0
    assert fake.streams and fake.streams[0].active is False


def test_microphone_recovery_uses_durable_endpoint_when_index_is_reused(
    monkeypatch,
    tmp_path,
) -> None:
    service, fake = _service(monkeypatch, tmp_path)
    service._audio_routes.remember(
        "input",
        index=1,
        name="Logitech G635 Microphone",
        host_api="Windows WASAPI",
        sample_rate=48000,
        channels=1,
    )

    index, name, rate = service.resolve_working_microphone(
        1,
        requested_name="Logitech G635 Microphone",
    )

    assert index == 2
    assert name == "Logitech G635 Microphone"
    assert rate == 48000
    assert fake.checked_input[0] == (2, 48000)
    saved = service._audio_routes.preference("input")
    assert saved is not None and saved.last_index == 2
    assert "2" in service._last_audio_recovery or "Logitech" in service._last_audio_recovery


def test_output_recovery_uses_saved_endpoint_and_supported_sample_rate(
    monkeypatch,
    tmp_path,
) -> None:
    service, fake = _service(monkeypatch, tmp_path)
    service._audio_routes.remember(
        "output",
        index=3,
        name="Logitech G635 Speakers",
        host_api="Windows WASAPI",
        sample_rate=24000,
        channels=2,
    )

    result = service.play_output_test_tone(3)

    assert result["index"] == 4
    assert result["name"] == "Logitech G635 Speakers"
    assert result["sample_rate"] == 48000
    assert (4, 24000) in fake.checked_output
    assert (4, 48000) in fake.checked_output
    assert fake.frames_written > 0
    saved = service._audio_routes.preference("output")
    assert saved is not None
    assert saved.last_index == 4
    assert saved.sample_rate == 48000
    assert "Logitech G635 Speakers" in service._last_audio_recovery


def test_output_does_not_replay_sentence_after_partial_write(monkeypatch, tmp_path) -> None:
    service, fake = _service(monkeypatch, tmp_path, partial_failure=True)

    with pytest.raises(RuntimeError, match="cümlenin başını tekrarlamamak"):
        service.play_output_test_tone(4)

    assert len(fake.streams) == 1
    assert fake.streams[0].writes == 2


def test_wdm_ks_endpoints_are_not_exposed_as_recovery_candidates(
    monkeypatch,
    tmp_path,
) -> None:
    service, _fake = _service(monkeypatch, tmp_path)

    assert all("wdm-ks" not in item.host_api.casefold() for item in service.microphones())
    assert all("wdm-ks" not in item.host_api.casefold() for item in service.output_devices())


def test_strict_piper_falls_back_to_windows_only_when_no_output_route_started(
    monkeypatch,
) -> None:
    from artmach_assistant.core.audio_device_resilience import AudioRouteUnavailableError

    service = VoiceService()
    calls: list[str] = []

    def fail_before_playback(*_args, **_kwargs):
        raise AudioRouteUnavailableError("Kullanılabilir ses çıkışı bulunamadı.")

    monkeypatch.setattr(service, "_speak_with_piper", fail_before_playback)
    monkeypatch.setattr(
        service,
        "_speak_with_windows",
        lambda *_args, **_kwargs: calls.append("windows") or "Tolga",
    )

    result = service.speak("Merhaba", backend="piper")

    assert calls == ["windows"]
    assert "Windows TTS kullanıldı" in result
    assert "Piper hatası" in result


def test_strict_piper_does_not_hide_synthesis_or_installation_errors(monkeypatch) -> None:
    service = VoiceService()
    windows_calls: list[str] = []

    def fail_synthesis(*_args, **_kwargs):
        raise RuntimeError("Piper modeli bulunamadı")

    monkeypatch.setattr(service, "_speak_with_piper", fail_synthesis)
    monkeypatch.setattr(
        service,
        "_speak_with_windows",
        lambda *_args, **_kwargs: windows_calls.append("windows") or "Tolga",
    )

    with pytest.raises(RuntimeError, match="Piper modeli bulunamadı"):
        service.speak("Merhaba", backend="piper")

    assert windows_calls == []
