from __future__ import annotations

from artmach_assistant.core.diagnostic_investigation import build_investigation


class _Evidence:
    def __init__(self, evidence_id, kind, source, confidence, subsystem):
        self.evidence_id = evidence_id
        self.kind = kind
        self.source = source
        self.summary = kind
        self.confidence = confidence
        self.subsystem = subsystem


def test_ranks_repeated_independent_evidence_as_root_cause() -> None:
    result = build_investigation(
        "voice",
        (
            _Evidence("E1", "invalid_sample_rate", "a.log", 90, "audio_output"),
            _Evidence("E2", "invalid_sample_rate", "b.log", 88, "audio_output"),
            _Evidence("E3", "whisper_failure", "a.log", 70, "speech_to_text"),
        ),
        health={"subsystems": [
            {"subsystem": "audio_output", "score": 20},
            {"subsystem": "speech_to_text", "score": 50},
        ]},
    )
    assert result.status == "root_cause_identified"
    assert result.root_cause is not None
    assert result.root_cause.cause == "invalid_sample_rate"
    assert result.root_cause.evidence_ids == ("E1", "E2")
    assert result.steps[0].completed is True


def test_close_hypotheses_require_more_investigation() -> None:
    result = build_investigation(
        "ui",
        (
            _Evidence("E1", "ui_thread_blocked", "a.log", 84, "responsiveness"),
            _Evidence("E2", "qt_layout_warning", "b.log", 83, "layout"),
        ),
    )
    assert result.status == "investigating"
    assert result.root_cause is None
    assert len(result.steps) == 2


def test_missing_evidence_creates_measurement_step() -> None:
    result = build_investigation("performance", (), measurement_action="Metrikleri topla.")
    assert result.status == "needs_evidence"
    assert result.steps[0].action == "Metrikleri topla."
    assert result.root_cause is None
