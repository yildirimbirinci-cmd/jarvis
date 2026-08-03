from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.diagnostic_engine import DiagnosticEngine


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for relative in (
        "app.py",
        "config.py",
        "core/voice_service.py",
        "core/audio_device_resilience.py",
        "core/voice_acceptance_service.py",
        "core/voice_turn_coordinator.py",
        "core/runtime_instrumentation.py",
        "core/runtime_observability.py",
        "core/runtime_diagnostics.py",
        "core/runtime_session.py",
        "core/gui_voice_integration.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    return root


def test_actionable_diagnosis_contains_root_cause_investigation(tmp_path: Path) -> None:
    root = _project(tmp_path)
    first = root / "first.log"
    second = root / "second.log"
    first.write_text("Invalid sample rate -9997\n", encoding="utf-8")
    second.write_text("Piper failed: invalid sample rate -9997\n", encoding="utf-8")

    report = DiagnosticEngine(root).diagnose(
        "Ses sorunlarını gider",
        log_paths=[first, second],
    )

    assert report.status == "actionable"
    assert report.investigation is not None
    assert report.investigation.status == "root_cause_identified"
    assert report.investigation.root_cause is not None
    assert report.investigation.root_cause.cause == "invalid_sample_rate"
    assert report.planner_task is not None
    assert report.planner_task["diagnostic_investigation"]["root_cause_hypothesis_id"]


def test_ambiguous_diagnosis_does_not_create_planner_task(tmp_path: Path) -> None:
    report = DiagnosticEngine(_project(tmp_path)).diagnose(
        "Arayüz donma ve layout sorununu analiz et",
        runtime_evidence=(
            {
                "domain": "ui",
                "subsystem": "responsiveness",
                "evidence_id": "UI-1",
                "summary": "Main thread blocked.",
                "confidence": 84,
            },
            {
                "domain": "ui",
                "subsystem": "layout",
                "evidence_id": "UI-2",
                "summary": "Layout warning repeated.",
                "confidence": 83,
            },
        ),
    )
    assert report.status == "investigating"
    assert report.planner_task is None
    assert report.investigation is not None
    assert report.investigation.root_cause is None


def test_investigation_is_serialized(tmp_path: Path) -> None:
    root = _project(tmp_path)
    log = root / "voice.log"
    log.write_text("Whisper timeout error\n", encoding="utf-8")
    report = DiagnosticEngine(root).diagnose("Whisper sorununu düzelt", log_paths=[log])
    payload = json.loads(report.write(tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["investigation"]["domain"] == "voice"
    assert payload["investigation"]["hypotheses"]
