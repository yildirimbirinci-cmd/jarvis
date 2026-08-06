from __future__ import annotations

from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_close_event_stops_audio_and_starts_bounded_async_shutdown() -> None:
    source = APP_PATH.read_text(encoding="utf-8")
    close_event = source.split("    def closeEvent(self, event) -> None:", 1)[1]
    close_event = close_event.split("\n\n# Jarvis turn-aware", 1)[0]

    assert "self.engine.voice.stop_speaking()" in close_event
    assert "self._stop_barge_in()" in close_event
    assert "self._start_async_shutdown()" in close_event
    assert "event.ignore()" in close_event
    assert ".wait(" not in close_event
