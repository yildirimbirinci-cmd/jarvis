from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.diagnostic_engine import DiagnosticEngine


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for relative in (
        "core/voice_service.py",
        "core/audio_device_resilience.py",
        "core/voice_acceptance_service.py",
        "core/voice_turn_coordinator.py",
        "core/runtime_instrumentation.py",
        "app.py",
        "config.py",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    return root


def test_actionable_report_contains_health_and_planner_snapshot(tmp_path: Path) -> None:
    root = _project(tmp_path)
    log = root / "voice.log"
    log.write_text("Invalid sample rate -9997\n", encoding="utf-8")
    report = DiagnosticEngine(root).diagnose("Ses sorunlarını gider", log_paths=[log])
    assert report.health is not None
    assert report.health.domain == "voice"
    assert report.health.score is not None
    assert report.health.status in {"critical", "degraded", "warning", "healthy"}
    assert report.planner_task["diagnostic_health"]["score"] == report.health.score


def test_needs_evidence_report_exposes_unknown_subsystem_health(tmp_path: Path) -> None:
    report = DiagnosticEngine(_project(tmp_path)).diagnose("Wake word sorununu incele")
    assert report.status == "needs_evidence"
    assert report.health is not None
    assert report.health.score is None
    assert report.health.subsystems[0].subsystem == "wake_word"
    assert report.health.subsystems[0].status == "unknown"


def test_health_is_serialized_in_report_json(tmp_path: Path) -> None:
    report = DiagnosticEngine(_project(tmp_path)).diagnose("Ses sorunlarını gider")
    output = report.write(tmp_path / "report.json")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["health"]["domain"] == "voice"
    assert payload["health"]["status"] == "unknown"
