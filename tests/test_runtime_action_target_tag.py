from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gui_conversation_action_is_explicitly_tagged() -> None:
    source = (ROOT / "core" / "gui_voice_integration.py").read_text(encoding="utf-8")
    assert "action_path" in source
    assert "action_symbol" in source
