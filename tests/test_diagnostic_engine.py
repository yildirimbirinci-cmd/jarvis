from __future__ import annotations

import json
from pathlib import Path

from artmach_assistant.core.diagnostic_engine import DiagnosticEngine


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "core").mkdir(parents=True)
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


def test_broad_voice_repair_request_is_recognised(tmp_path: Path) -> None:
    engine = DiagnosticEngine(_project(tmp_path))
    assert engine.recognises_request("Jarvis, ses sorunlarını gider")
    assert engine.recognises_request("Piper TTS hatasını analiz et")
    assert not engine.recognises_request("Hava nasıl?")


def test_no_evidence_produces_measurement_not_fake_root_cause(tmp_path: Path) -> None:
    engine = DiagnosticEngine(_project(tmp_path))
    report = engine.diagnose("Ses sorunlarını gider")
    assert report.status == "needs_evidence"
    assert report.planner_task is None
    assert report.findings[0].requires_measurement is True
    assert "kök nedeni" in report.findings[0].explanation


def test_invalid_sample_rate_log_creates_actionable_task(tmp_path: Path) -> None:
    root = _project(tmp_path)
    log = root / "logs" / "voice.log"
    log.parent.mkdir()
    log.write_text("Piper playback failed: Invalid sample rate -9997\n", encoding="utf-8")
    report = DiagnosticEngine(root).diagnose("Ses sorunlarını gider", log_paths=[log])
    assert report.status == "actionable"
    assert report.findings[0].subsystem == "audio_output"
    assert report.findings[0].confidence >= 90
    assert report.planner_task is not None
    assert report.planner_task["requires_experiment"] is True
    assert "core/voice_service.py" in report.planner_task["affected_files"]


def test_specific_request_limits_subsystems(tmp_path: Path) -> None:
    report = DiagnosticEngine(_project(tmp_path)).diagnose("Wake word sorununu incele")
    assert report.subsystems == ("wake_word",)


def test_runtime_evidence_can_drive_planner_task(tmp_path: Path) -> None:
    report = DiagnosticEngine(_project(tmp_path)).diagnose(
        "Whisper ses algılama sorununu çöz",
        runtime_evidence=[{
            "evidence_id": "RUN-STT-1",
            "source": "runtime",
            "summary": "Whisper timeout oranı son 10 denemede yüzde 60.",
            "confidence": 91,
        }],
    )
    assert report.status == "actionable"
    assert report.planner_task is not None
    assert report.planner_task["evidence_ids"] == ["RUN-STT-1"]


def test_report_round_trip_json(tmp_path: Path) -> None:
    root = _project(tmp_path)
    report = DiagnosticEngine(root).diagnose("Ses sorunlarını gider")
    path = report.write(tmp_path / "diagnostic.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["domain"] == "voice"
