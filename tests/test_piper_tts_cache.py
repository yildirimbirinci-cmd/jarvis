from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from artmach_assistant.core import voice_service
from artmach_assistant.core.voice_service import VoiceService


class _SoundDevice:
    def __init__(self):
        self.peaks = []

    def query_devices(self, *args, **kwargs):
        return {"default_samplerate": 22050}

    def play(self, audio, *, samplerate, device):
        assert audio.size > 0
        assert samplerate == 22050
        self.peaks.append(float(np.max(np.abs(audio))))

    def wait(self):
        return None


def test_piper_reuses_rendered_audio(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "piper.exe"
    model = tmp_path / "voice.onnx"
    config = tmp_path / "voice.onnx.json"
    executable.write_bytes(b"exe")
    model.write_bytes(b"model")
    config.write_text("{}", encoding="utf-8")
    calls = []

    def render(command, _text, **_kwargs):
        calls.append(command)
        output = Path(command[command.index("--output_file") + 1])
        with wave.open(str(output), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            samples = np.tile(
                np.asarray([32767, -32768], dtype=np.int16), 1103
            )
            wav_file.writeframes(samples.tobytes())
        return subprocess.CompletedProcess(command, 0, "", "")

    service = VoiceService()
    sound_device = _SoundDevice()
    monkeypatch.setattr(
        service, "_discover_piper", lambda *_args: (executable, model)
    )
    monkeypatch.setattr(service, "_sounddevice", lambda: sound_device)
    monkeypatch.setattr(service, "_numpy", lambda: np)
    monkeypatch.setattr(voice_service, "PIPER_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(service, "_run_cancellable_piper_process", render)

    first = service._speak_with_piper(
        "Dinliyorum.", "", "", None, volume=50
    )
    second = service._speak_with_piper(
        "Dinliyorum.", "", "", None, volume=50
    )

    assert len(calls) == 1
    assert "--length_scale" in calls[0]
    assert calls[0][calls[0].index("--length_scale") + 1] == "0.640"
    assert sound_device.peaks
    assert max(sound_device.peaks) <= 0.441
    assert "önbelleği" not in first
    assert "hazır ses önbelleği" in second


def test_invalid_windows_piper_is_rejected_before_process_start(tmp_path: Path) -> None:
    executable = tmp_path / "piper.exe"
    executable.write_bytes(b"exe")

    service = VoiceService()
    with pytest.raises(RuntimeError, match="Windows PE"):
        service._validate_piper_executable(executable, windows=True)
