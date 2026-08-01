from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.audio_hardware_acceptance import (
    AudioHardwareAcceptanceReport,
    run_audio_hardware_acceptance,
)


class _Voice:
    def microphones(self):
        return [
            SimpleNamespace(
                index=9,
                name="Logitech G635 Microphone",
                host_api="Windows WASAPI",
            )
        ]

    def output_devices(self):
        return [
            SimpleNamespace(
                index=12,
                name="Monitor Speakers",
                host_api="Windows WASAPI",
            )
        ]

    def resolve_working_microphone(self, requested_index, requested_name="", status_callback=None):
        return 9, "Logitech G635 Microphone", 48000

    def probe_output_device(self, output_device=None):
        return {
            "index": 12,
            "name": "Monitor Speakers",
            "host_api": "Windows WASAPI",
            "sample_rate": 48000,
            "channels": 2,
        }

    def tts_backend_status(self, backend="auto", piper_executable="", piper_model=""):
        return {
            "backend": backend,
            "ready": True,
            "piper_ready": False,
            "piper_detail": "Piper kurulu değil",
            "windows_ready": True,
            "windows_detail": "2 yerel Windows sesi",
        }

    def audio_route_status(self):
        return {
            "input": {"name": "Logitech G635 Microphone"},
            "output": {"name": "Monitor Speakers"},
            "last_recovery": "Çıkış indeksi düzeltildi.",
            "store_error": "",
        }


def test_real_hardware_acceptance_report_covers_input_output_tts_and_routes() -> None:
    report = run_audio_hardware_acceptance(
        _Voice(),
        microphone_index=4,
        microphone_name="Logitech G635 Microphone",
        output_index=3,
        tts_backend="auto",
    )

    assert report.ok is True
    assert len(report.checks) == 6
    rendered = report.render()
    assert "BAŞARILI" in rendered
    assert "Logitech G635 Microphone" in rendered
    assert "Monitor Speakers" in rendered
    assert "TTS yedekleme yolu" in rendered
    assert "kullanıcı doğrulamalıdır" in rendered


def test_hardware_acceptance_report_never_hides_failures() -> None:
    report = AudioHardwareAcceptanceReport()
    report.add("mikrofon", False, "aygıt yok")

    assert report.ok is False
    assert "BAŞARISIZ" in report.render()
    assert "[HATA] mikrofon: aygıt yok" in report.render()
