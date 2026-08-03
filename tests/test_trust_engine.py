from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.trust_engine import ApprovalTrustEngine


def _promotion(tmp_path: Path, *, full_exit: int = 0, checkpoint: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    result = tmp_path / "promotion.json"
    result.write_text(json.dumps({
        "schema_version": 1,
        "promotion_id": "promo-1",
        "experiment_id": "exp-1",
        "candidate_id": "cand-1",
        "status": "promoted",
        "project_root": str(root),
        "risk": "low",
        "rolled_back": False,
        "checkpoint_root": str(tmp_path / "checkpoint") if checkpoint else "",
        "files": [{
            "relative_path": "core/example.py",
            "before_digest": "0" * 64,
            "after_digest": "1" * 64,
            "checkpoint_path": str(tmp_path / "checkpoint" / "core/example.py") if checkpoint else "",
        }],
        "commands": [
            {"name": "focused_tests", "exit_code": 0, "output": "3 passed in 0.1s"},
            {"name": "full_tests", "exit_code": full_exit, "output": "1873 passed, 9 skipped" if full_exit == 0 else "1 failed, 1872 passed"},
        ],
    }), encoding="utf-8")
    return result


def _diagnostic(tmp_path: Path, *, confidence: int = 95, root: bool = True) -> Path:
    path = tmp_path / "diagnostic.json"
    root_id = "hyp-root" if root else None
    path.write_text(json.dumps({
        "investigation": {
            "status": "root_cause_identified" if root else "investigating",
            "root_cause_hypothesis_id": root_id,
            "hypotheses": [
                {
                    "hypothesis_id": "hyp-root",
                    "subsystem": "text_to_speech",
                    "cause": "invalid_sample_rate",
                    "confidence": confidence,
                    "evidence_ids": ["log-1", "log-2"],
                    "explanation": "Piper örnekleme hızı cihaz kapasitesiyle uyuşmuyor.",
                },
                {
                    "hypothesis_id": "hyp-alt",
                    "subsystem": "audio_output",
                    "cause": "wrong_device",
                    "confidence": 55,
                    "evidence_ids": ["log-3"],
                },
            ],
        }
    }), encoding="utf-8")
    return path


def test_recommends_approval_with_strong_evidence_tests_and_rollback(tmp_path: Path) -> None:
    report = ApprovalTrustEngine(
        _promotion(tmp_path),
        diagnostic_report_path=_diagnostic(tmp_path),
    ).build()
    assert report.recommendation == "approve"
    assert report.scorecard.overall_score >= 80
    assert report.scorecard.test_score == 100
    assert report.scorecard.rollback_score == 100
    assert report.alternatives_considered == ("audio_output: wrong_device",)
    assert Path(report.report_path).is_file()


def test_holds_when_full_regression_failed(tmp_path: Path) -> None:
    report = ApprovalTrustEngine(
        _promotion(tmp_path, full_exit=1),
        diagnostic_report_path=_diagnostic(tmp_path),
    ).build()
    assert report.recommendation == "hold"
    assert report.scorecard.test_score == 0
    assert any("Tam regresyon" in item for item in report.warnings)


def test_reviews_when_diagnostic_report_is_missing(tmp_path: Path) -> None:
    report = ApprovalTrustEngine(_promotion(tmp_path)).build()
    assert report.recommendation == "review"
    assert report.scorecard.evidence_score == 0
    assert any("Kök neden" in item for item in report.warnings)


def test_holds_without_rollback_checkpoint(tmp_path: Path) -> None:
    report = ApprovalTrustEngine(
        _promotion(tmp_path, checkpoint=False),
        diagnostic_report_path=_diagnostic(tmp_path),
    ).build()
    assert report.recommendation == "hold"
    assert report.scorecard.rollback_score == 0
