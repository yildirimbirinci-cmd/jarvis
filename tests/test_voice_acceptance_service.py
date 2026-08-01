from __future__ import annotations

from artmach_assistant.core.voice_acceptance_service import (
    VoiceAcceptanceReport,
    run_voice_acceptance_contract,
)


def test_hardware_free_voice_acceptance_contract_passes() -> None:
    report = run_voice_acceptance_contract()

    assert report.ok is True
    assert len(report.checks) == 7
    assert all(check.ok for check in report.checks)
    rendered = report.render()
    assert "BAŞARILI" in rendered
    assert "geç kalan model cevabı" in rendered
    assert "geç kalan TTS callback'i" in rendered
    assert "gerçek Windows cihazında" in rendered


def test_acceptance_report_does_not_hide_failed_check() -> None:
    report = VoiceAcceptanceReport()
    report.add("örnek", False, "kanıt")

    assert report.ok is False
    rendered = report.render()
    assert "BAŞARISIZ" in rendered
    assert "[HATA] örnek: kanıt" in rendered
