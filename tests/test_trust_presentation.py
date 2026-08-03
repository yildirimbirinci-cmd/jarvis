from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.trust_presentation import ApprovalTrustPresenter


def _report(tmp_path: Path, *, recommendation: str = "approve") -> Path:
    path = tmp_path / "trust.json"
    score = 91 if recommendation == "approve" else 42
    warnings = [] if recommendation == "approve" else ["Tam regresyon doğrulaması eksik veya başarısız."]
    path.write_text(json.dumps({
        "recommendation": recommendation,
        "recommendation_reason": "Eşikler değerlendirildi.",
        "scorecard": {
            "evidence_score": 95,
            "test_score": 100 if recommendation == "approve" else 0,
            "risk_score": 15,
            "rollback_score": 100,
            "scope_score": 100,
            "overall_score": score,
        },
        "root_cause_summary": "Piper örnekleme hızı cihaz kapasitesiyle uyuşmuyor.",
        "evidence_ids": ["log-1", "log-2"],
        "alternatives_considered": ["audio_output: wrong_device"],
        "changed_files": ["core/voice_service.py"],
        "focused_tests": "passed=8; failed=0; skipped=0; exit=0",
        "full_tests": "passed=1878; failed=0; skipped=9; exit=0",
        "impact_summary": "1 dosya değişti",
        "rollback_summary": "Rollback hazır: checkpoint",
        "approval_checklist": ["Kök nedeni incele.", "Davranışı manuel kontrol et."],
        "warnings": warnings,
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_builds_owner_friendly_approve_summary(tmp_path: Path) -> None:
    presentation = ApprovalTrustPresenter(_report(tmp_path)).build()
    assert presentation.recommendation == "approve"
    assert "Onay öneriliyor" in presentation.headline
    assert "91/100" in presentation.headline
    assert "Piper" in presentation.voice_summary
    assert "core/voice_service.py" in presentation.change_lines
    assert Path(presentation.presentation_path).is_file()


def test_hold_never_uses_approval_language(tmp_path: Path) -> None:
    presentation = ApprovalTrustPresenter(_report(tmp_path, recommendation="hold")).build()
    assert presentation.recommendation == "hold"
    assert "Onaylama önerilmiyor" in presentation.headline
    assert "onaylama" in presentation.voice_summary.lower()
    assert "onayını verebilirsin" not in presentation.decision_guidance.lower()
    assert presentation.warning_lines


def test_unknown_recommendation_fails_closed(tmp_path: Path) -> None:
    presentation = ApprovalTrustPresenter(_report(tmp_path, recommendation="mystery")).build()
    assert presentation.recommendation == "hold"
