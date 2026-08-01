from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def test_audio_hardware_command_is_local_and_precedes_other_routes() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.normalize_address = lambda text: str(text)
    engine.command_key = lambda text: " ".join(str(text).casefold().split())
    engine.audio_hardware_acceptance_report = lambda: "DONANIM_RAPORU"

    assert (
        AssistantEngine.handle_local_command(engine, "ses donanimi kabul testi")
        == "DONANIM_RAPORU"
    )


class _Voice:
    def microphones(self):
        return [SimpleNamespace(index=1, name="Mic", host_api="MME")]

    def output_devices(self):
        return [SimpleNamespace(index=2, name="Speaker", host_api="MME")]

    def resolve_working_microphone(self, requested_index, requested_name="", status_callback=None):
        assert requested_index == 7
        assert requested_name == "Saved Mic"
        return 1, "Mic", 44100

    def probe_output_device(self, output_device=None):
        assert output_device == 8
        return {
            "index": 2,
            "name": "Speaker",
            "host_api": "MME",
            "sample_rate": 44100,
            "channels": 2,
        }

    def tts_backend_status(self, backend="auto", piper_executable="", piper_model=""):
        assert backend == "auto"
        assert piper_executable == "piper.exe"
        assert piper_model == "voice.onnx"
        return {
            "backend": backend,
            "ready": True,
            "piper_ready": True,
            "piper_detail": "hazır",
            "windows_ready": True,
            "windows_detail": "hazır",
        }

    def audio_route_status(self):
        return {
            "input": {"name": "Mic"},
            "output": {"name": "Speaker"},
            "last_recovery": "",
            "store_error": "",
        }


def test_engine_passes_saved_audio_configuration_to_hardware_acceptance() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.voice = _Voice()
    engine.config = SimpleNamespace(
        voice_microphone_index=7,
        voice_microphone_name="Saved Mic",
        voice_output_index=8,
        voice_tts_backend="auto",
        piper_executable="piper.exe",
        piper_model="voice.onnx",
    )

    rendered = engine.audio_hardware_acceptance_report()

    assert "BAŞARILI" in rendered
    assert "Speaker" in rendered
