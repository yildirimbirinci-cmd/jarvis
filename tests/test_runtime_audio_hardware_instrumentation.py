from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def _reset_instrumentation():
    instrumentation = importlib.import_module(
        "artmach_assistant.core.runtime_instrumentation"
    )
    instrumentation.reset_runtime_instrumentation_for_tests()
    yield
    instrumentation.reset_runtime_instrumentation_for_tests()


def test_audio_hardware_entry_points_are_observable_without_raw_audio(monkeypatch, tmp_path) -> None:
    instrumentation = importlib.import_module(
        "artmach_assistant.core.runtime_instrumentation"
    )
    voice_type = importlib.import_module(
        "artmach_assistant.core.voice_service"
    ).VoiceService
    events: list[dict[str, object]] = []

    monkeypatch.setattr(
        voice_type,
        "probe_output_device",
        lambda self, output_device=None: {
            "index": output_device,
            "name": "Speaker",
            "sample_rate": 48000,
        },
    )
    monkeypatch.setattr(
        voice_type,
        "tts_backend_status",
        lambda self, *args, **kwargs: {"ready": True},
    )
    instrumentation.configure_runtime_instrumentation(
        lambda **payload: events.append(payload) or True,
        workspace_provider=lambda: tmp_path,
    )
    instrumentation.install_runtime_instrumentation()

    voice = voice_type()
    assert voice.probe_output_device(6)["index"] == 6
    assert voice.tts_backend_status("auto")["ready"] is True

    by_action = {event["action"]: event for event in events}
    assert by_action["audio_output_probe"]["metadata"]["output_device"] == 6
    assert by_action["tts_backend_readiness"]["status"] == "completed"
    assert "raw_audio" not in repr(events)
    coverage = instrumentation.runtime_instrumentation_coverage()
    assert any(name.endswith("VoiceService.probe_output_device") for name in coverage)
