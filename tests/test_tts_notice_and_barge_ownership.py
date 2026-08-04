from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_maintenance_notice_is_not_appended_to_final_answer() -> None:
    source = (ROOT / "core" / "assistant.py").read_text(encoding="utf-8")
    assert 'self._pending_maintenance_notice = maintenance_note or ""' in source
    assert 'final_answer = f"{final_answer}\n\n{maintenance_note}"' not in source
    assert "def take_pending_maintenance_notice" in source


def test_gui_and_fallback_render_maintenance_as_separate_notification() -> None:
    for relative in ("app.py", "core/gui_voice_integration.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "take_pending_maintenance_notice" in source
        assert "callable(take_notice)" in source
        assert "BAKIM BİLDİRİMİ:" in source


def test_only_answer_barge_listener_announces_readiness() -> None:
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'if self.source == "answer":' in source
    assert source.count("Diyalog kesme dinleyicisi hazır; 'dur' diyebilirsin.") == 1
