from __future__ import annotations

from pathlib import Path

import pytest

from artmach_assistant.core.runtime_observability import (
    RuntimeEventStore,
    RuntimeHealthAnalyzer,
)


def test_event_store_redacts_secrets_and_preserves_code_location(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json", keep=100)

    event = store.record(
        component="VoiceService",
        action="speak",
        status="failed",
        workspace=tmp_path,
        scope="own_code",
        source_path="core/voice_service.py",
        symbol="VoiceService.speak",
        message="token=abcdef123456 password=hunter2 audio failed",
        error_type="PortAudioError",
        metadata={"api_key": "secret-value", "device": 7},
    )

    assert "abcdef123456" not in event.message
    assert "hunter2" not in event.message
    assert event.source_path == "core/voice_service.py"
    assert event.symbol == "VoiceService.speak"
    loaded = store.load()
    assert loaded == (event,)
    assert loaded[0].metadata["api_key"] == "<redacted>"


def test_repeated_failures_become_actionable_runtime_finding(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _index in range(3):
        store.record(
            component="VoiceService",
            action="speak",
            status="failed",
            workspace=tmp_path,
            scope="own_code",
            source_path="core/voice_service.py",
            symbol="VoiceService.speak",
            message="Invalid sample rate 44100",
            error_type="PortAudioError",
        )

    report = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)

    assert report.failed_count == 3
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.finding_id.startswith("RUN-")
    assert finding.category == "repeated_runtime_failure"
    assert finding.occurrence_count == 3
    assert finding.affected_paths == ("core/voice_service.py",)
    assert finding.affected_symbols == ("VoiceService.speak",)
    converted = finding.to_improvement_finding()
    assert converted.finding_id == finding.finding_id
    assert converted.affected_paths == finding.affected_paths


def test_repeated_slow_operation_is_measured_without_calling_it_an_error(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for duration in (5200.0, 6100.0, 7000.0):
        store.record(
            component="ProjectImprovementRuntime",
            action="assessment",
            status="completed",
            duration_ms=duration,
            workspace=tmp_path,
            scope="project",
            source_path="core/project_improvement_runtime.py",
            symbol="ProjectImprovementRuntime.assessment",
        )

    report = RuntimeHealthAnalyzer(store, slow_threshold_ms=5000).analyze(
        workspace=tmp_path
    )

    assert report.failed_count == 0
    assert len(report.findings) == 1
    assert report.findings[0].category == "repeated_slow_operation"
    assert "6100" in report.findings[0].explanation


def test_corrupt_event_store_is_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    path.write_text('{"schema_version":1,"events":[', encoding="utf-8")
    store = RuntimeEventStore(path)

    assert store.load() == ()
    assert not path.exists()
    assert list(tmp_path.glob("events.corrupt_*.json"))


def test_observe_records_failure_and_reraises(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")

    with pytest.raises(RuntimeError, match="boom"):
        with store.observe(
            component="AssistantEngine",
            action="handle",
            workspace=tmp_path,
            source_path="core/assistant.py",
            symbol="AssistantEngine.handle",
        ):
            raise RuntimeError("boom")

    events = store.load()
    assert len(events) == 1
    assert events[0].status == "failed"
    assert events[0].error_type == "RuntimeError"
