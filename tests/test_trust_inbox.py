import json
from pathlib import Path
from artmach_assistant.core.trust_inbox import TrustApprovalInbox


def _write(root: Path, name: str, recommendation: str = "approve") -> Path:
    path = root / name / "approval_trust_presentation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "recommendation": recommendation,
        "headline": "Onay öneriliyor — güven puanı 94/100",
        "short_summary": "Testler temiz.",
        "voice_summary": "Onay öneriliyor. Genel güven puanı yüzde doksan dört.",
        "decision_guidance": "Dosyaları kontrol et.",
        "change_lines": ["core/example.py"],
        "warning_lines": [],
    }), encoding="utf-8")
    return path


def test_inbox_lists_and_renders_reports(tmp_path: Path) -> None:
    _write(tmp_path, "one")
    inbox = TrustApprovalInbox((tmp_path,))
    assert inbox.latest() is not None
    assert "core/example.py" in inbox.render_text()
    assert "Onay öneriliyor" in inbox.render_text()


def test_inbox_ignores_invalid_and_missing_reports(tmp_path: Path) -> None:
    bad = tmp_path / "bad" / "approval_trust_presentation.json"
    bad.parent.mkdir()
    bad.write_text("[]", encoding="utf-8")
    assert TrustApprovalInbox((tmp_path,)).list_items() == ()
