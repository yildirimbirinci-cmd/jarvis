from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.voice_service import VoiceService


VOICE_SERVICE_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "voice_service.py"
)


def test_piper_long_reply_is_split_before_synthesis_and_playback() -> None:
    source = VOICE_SERVICE_PATH.read_text(encoding="utf-8")
    method = source.split("    def _speak_with_piper(\n", 1)[1].split(
        "\n    def prepare_speech(\n", 1
    )[0]

    assert "chunks = self._sentence_chunks(text)" in method
    assert "chunks = [text]" not in method
    assert method.index("for chunk in chunks:") < method.index(
        "self._play_audio_resilient("
    )
    assert method.count("self._speech_cancelled(cancel_event, cancel_check)") >= 4


def test_capability_reply_produces_a_short_first_piper_chunk() -> None:
    text = (
        "Etkin yerel becerilerim: Sesli etkileşim; Uygulama işlemleri; "
        "Masaüstü klasörleri; Kalıcı öğrenme; Yerel diyalog; Çalışma durumu."
    )

    chunks = VoiceService._sentence_chunks(text)

    assert len(chunks) >= 2
    assert len(chunks[0]) <= 72
    assert "Sesli etkileşim" in chunks[0]


def test_maintenance_diagnostic_stays_out_of_spoken_reply() -> None:
    service = VoiceService()
    visible = (
        "Etkin yerel becerilerim: Sesli etkileşim. "
        "Bakım uyarısı [RUN-D031FA1A36]: Tekrarlanan yavas islem: "
        "VoiceService.audio_output_playback."
    )

    spoken = service._prepare_tts_text(visible)

    assert spoken == "Etkin yerel becerilerim: Sesli etkileşim."
    assert "RUN-" not in spoken
    assert "Bakım uyarısı" not in spoken
