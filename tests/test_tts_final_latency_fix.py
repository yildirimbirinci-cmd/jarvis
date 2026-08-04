from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_spoken_response_strips_inline_maintenance_alert() -> None:
    source = (ROOT / "core" / "assistant.py").read_text(encoding="utf-8")
    assert "re.split(" in source
    maintenance_label = "Bak\u0131m uyar\u0131s\u0131"
    assert maintenance_label in source
    assert r"\s*\[RUN-" in source


def test_gui_releases_thinking_barge_before_tts() -> None:
    source = (
        ROOT / "core" / "gui_voice_integration.py"
    ).read_text(encoding="utf-8")

    raw_index = source.index("raw_answer = str(answer)")
    stop_index = source.index(
        "self._stop_barge_in(turn_id=active_turn or None)",
        raw_index,
    )
    packet_index = source.index("packet = None", stop_index)

    assert raw_index < stop_index < packet_index


def test_fallback_app_releases_barge_before_tts() -> None:
    source = (ROOT / "app.py").read_text(encoding="utf-8")

    raw_index = source.index("raw_answer = str(answer)")
    stop_index = source.index("self._stop_barge_in()", raw_index)
    hidden_index = source.index("is_hidden = raw_answer", stop_index)

    assert raw_index < stop_index < hidden_index
