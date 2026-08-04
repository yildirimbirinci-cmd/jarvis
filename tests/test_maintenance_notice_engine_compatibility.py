from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gui_notice_lookup_is_optional_for_test_engines() -> None:
    source = (ROOT / "core" / "gui_voice_integration.py").read_text(encoding="utf-8")
    assert 'getattr(\n                self.engine, "take_pending_maintenance_notice", None' in source
    assert "if callable(take_notice)" in source


def test_fallback_app_notice_lookup_is_optional_for_test_engines() -> None:
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'getattr(\n                self.engine, "take_pending_maintenance_notice", None' in source
    assert "if callable(take_notice)" in source
