from __future__ import annotations

from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_close_event_waits_for_every_gui_owned_qthread() -> None:
    source = APP_PATH.read_text(encoding="utf-8")
    close_event = source.split("    def closeEvent(self, event) -> None:", 1)[1]
    close_event = close_event.split("\n\n\n# Jarvis turn-aware", 1)[0]

    assert "self.engine.voice.stop_speaking()" in close_event
    assert "self._stop_barge_in()" in close_event
    assert "(self.tts_worker, 8000)" in close_event
    assert "(self.worker, 8000)" in close_event
    assert "(self.wake_worker, 8000)" in close_event
    assert "event.ignore()" in close_event
    assert "QTimer.singleShot(250, self.close)" in close_event
