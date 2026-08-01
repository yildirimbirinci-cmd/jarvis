from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_instrumentation(monkeypatch):
    instrumentation = importlib.import_module("artmach_assistant.core.runtime_instrumentation")
    instrumentation.reset_runtime_instrumentation_for_tests()
    yield
    instrumentation.reset_runtime_instrumentation_for_tests()


def _runtime_types():
    assistant_module = importlib.import_module("artmach_assistant.core.assistant")
    instrumentation = importlib.import_module("artmach_assistant.core.runtime_instrumentation")
    return assistant_module.AssistantEngine, instrumentation


class _Store:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def record(self, **payload):
        self.rows.append(payload)
        return object()


def test_assistant_recorder_forwards_correlation_id(tmp_path: Path) -> None:
    AssistantEngine, _ = _runtime_types()
    engine = AssistantEngine.__new__(AssistantEngine)
    store = _Store()
    engine.runtime_events = store

    assert engine.record_runtime_event(
        component="VoiceService",
        action="speech_turn",
        status="completed",
        workspace=tmp_path,
        scope="voice",
        source_path="core/voice_service.py",
        symbol="VoiceService.listen_utterance",
        correlation_id="abc123",
        error_type="",
        metadata={"transcript_chars": 5},
    ) is True

    assert store.rows[0]["correlation_id"] == "abc123"
    assert store.rows[0]["error_type"] == ""
    assert store.rows[0]["metadata"] == {"transcript_chars": 5}


def test_own_code_validation_steps_emit_stable_failure_types(monkeypatch, tmp_path: Path) -> None:
    AssistantEngine, instrumentation = _runtime_types()
    events: list[dict[str, object]] = []

    monkeypatch.setattr(
        AssistantEngine,
        "_compile_own_code",
        lambda self: (False, "SyntaxError in core/example.py"),
    )
    monkeypatch.setattr(
        AssistantEngine,
        "_runtime_health_check",
        lambda self: (False, "application import failed"),
    )
    monkeypatch.setattr(
        AssistantEngine,
        "_run_own_tests",
        lambda self: (False, "FAILED tests/test_example.py::test_case"),
    )
    instrumentation.configure_runtime_instrumentation(
        lambda **payload: events.append(payload),
        workspace_provider=lambda: tmp_path,
    )
    instrumentation.install_runtime_instrumentation()

    engine = AssistantEngine.__new__(AssistantEngine)
    assert engine._compile_own_code()[0] is False
    assert engine._runtime_health_check()[0] is False
    assert engine._run_own_tests()[0] is False

    by_action = {str(event["action"]): event for event in events}
    assert by_action["own_code_compile"]["error_type"] == "OwnCodeCompileError"
    assert by_action["own_code_startup_check"]["error_type"] == "OwnCodeStartupError"
    assert by_action["own_code_tests"]["error_type"] == "OwnCodeTestError"
    assert by_action["own_code_tests"]["metadata"]["succeeded"] is False
    assert "FAILED tests/test_example.py::test_case" not in repr(
        by_action["own_code_tests"]["metadata"]
    )
