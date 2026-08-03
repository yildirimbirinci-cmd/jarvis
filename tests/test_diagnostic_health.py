from __future__ import annotations

from artmach_assistant.core.diagnostic_engine import DiagnosticEvidence
from artmach_assistant.core.diagnostic_health import build_health_summary


def test_no_evidence_produces_unknown_health() -> None:
    summary = build_health_summary("ui", ("layout", "responsiveness"), ())
    assert summary.score is None
    assert summary.status == "unknown"
    assert [item.status for item in summary.subsystems] == ["unknown", "unknown"]


def test_strong_evidence_marks_subsystem_critical() -> None:
    summary = build_health_summary(
        "voice",
        ("audio_output", "wake_word"),
        (
            DiagnosticEvidence("E1", "invalid_sample_rate", "voice.log", "failure", 95, "voice", "audio_output"),
            DiagnosticEvidence("E2", "invalid_sample_rate", "voice.log", "repeat", 90, "voice", "audio_output"),
        ),
    )
    output = summary.subsystems[0]
    assert output.score is not None and output.score <= 30
    assert output.status == "critical"
    assert output.evidence_count == 2
    assert summary.score == output.score
    assert summary.status == "critical"


def test_domain_score_uses_worst_measured_subsystem() -> None:
    summary = build_health_summary(
        "performance",
        ("latency", "memory"),
        (
            DiagnosticEvidence("LAT", "runtime", "runtime", "slow", 55, "performance", "latency"),
            DiagnosticEvidence("MEM", "runtime", "runtime", "leak", 92, "performance", "memory"),
        ),
    )
    scores = {item.subsystem: item.score for item in summary.subsystems}
    assert summary.score == min(score for score in scores.values() if score is not None)
    assert scores["memory"] < scores["latency"]
