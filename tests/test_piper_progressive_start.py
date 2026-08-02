from __future__ import annotations

from pathlib import Path


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
