from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.diagnostic_engine import DiagnosticEngine


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    files = (
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
        "core/git_workspace_service.py",
        "core/git_change_service.py",
        "core/approval_gate.py",
        "core/push_gate.py",
        "core/knowledge_repository.py",
        "core/repository_health_knowledge.py",
        "core/research_manager.py",
        "core/research_journal_closeout.py",
        "core/conversation_runtime.py",
        "core/local_dialogue.py",
        "core/background_refactoring_queue.py",
        "core/self_improvement_scheduler.py",
    )
    for relative in files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    return root


def test_ui_request_without_evidence_requests_measurement(tmp_path: Path) -> None:
    report = DiagnosticEngine(_project(tmp_path)).diagnose("Arayüzü modernleştir ve donma sorununu gider")
    assert report.domain == "ui"
    assert report.status == "needs_evidence"
    assert report.planner_task is None
    assert "responsiveness" in report.subsystems


def test_git_log_creates_scoped_planner_task(tmp_path: Path) -> None:
    root = _project(tmp_path)
    log = root / "logs" / "git.log"
    log.parent.mkdir()
    log.write_text("push rejected (non-fast-forward); fetch first\n", encoding="utf-8")
    report = DiagnosticEngine(root).diagnose("Git push sorununu çöz", log_paths=[log])
    assert report.domain == "git"
    assert report.status == "actionable"
    assert report.findings[0].subsystem == "push"
    assert report.planner_task["diagnostic_domain"] == "git"
    assert "core/push_gate.py" in report.planner_task["affected_files"]


def test_performance_runtime_evidence_is_domain_scoped(tmp_path: Path) -> None:
    report = DiagnosticEngine(_project(tmp_path)).diagnose(
        "Performans yavaşlığını analiz et",
        runtime_evidence=[{
            "domain": "performance",
            "subsystem": "memory",
            "evidence_id": "PERF-1",
            "summary": "RAM usage increased continuously for 30 minutes.",
            "confidence": 93,
        }],
    )
    assert report.status == "actionable"
    assert report.findings[0].subsystem == "memory"
    assert report.planner_task["evidence_ids"] == ["PERF-1"]


def test_audio_backward_compatibility(tmp_path: Path) -> None:
    root = _project(tmp_path)
    log = root / "voice.log"
    log.write_text("Invalid sample rate -9997\n", encoding="utf-8")
    report = DiagnosticEngine(root).diagnose("Ses sorunlarını gider", log_paths=[log])
    assert report.domain == "voice"
    assert report.findings[0].subsystem == "audio_output"
