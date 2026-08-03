from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_assistant_routes_trust_report_voice_commands() -> None:
    source = (ROOT / "core" / "assistant.py").read_text(encoding="utf-8")
    assert "def _trust_approval_report_request" in source
    assert "onay raporunu oku" in source
    assert "Sesli kısa komutla commit onayı vermiyorum" in source


def test_app_exposes_trust_approval_tab() -> None:
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'self.tabs.addTab(approval_tab, "Onay Merkezi")' in source
    assert "def refresh_trust_approval_panel" in source
