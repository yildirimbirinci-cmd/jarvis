from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _reset_instrumentation(monkeypatch):
    instrumentation = importlib.import_module("artmach_assistant.core.runtime_instrumentation")
    instrumentation.reset_runtime_instrumentation_for_tests()
    yield
    instrumentation.reset_runtime_instrumentation_for_tests()


def _instrumentation():
    return importlib.import_module("artmach_assistant.core.runtime_instrumentation")


def test_install_is_idempotent_and_recorder_failure_is_fail_open(monkeypatch, tmp_path: Path) -> None:
    instrumentation = _instrumentation()
    dialogue_module = importlib.import_module("artmach_assistant.core.local_dialogue")
    dialogue_type = dialogue_module.LocalDialogueManager
    monkeypatch.setattr(dialogue_type, "respond", lambda self, *args, **kwargs: "yanıt")

    def broken_recorder(**payload):
        raise RuntimeError("telemetry unavailable")

    instrumentation.configure_runtime_instrumentation(
        broken_recorder,
        workspace_provider=lambda: tmp_path,
    )
    first = instrumentation.install_runtime_instrumentation()
    second = instrumentation.install_runtime_instrumentation()

    assert first >= 30
    assert second == 0
    assert len(instrumentation.runtime_instrumentation_coverage()) == first
    dialogue = dialogue_type("chat-model", "http://127.0.0.1:11434")
    assert dialogue.respond("özel içerik") == "yanıt"


def test_nested_voice_operations_share_one_correlation_id(monkeypatch, tmp_path: Path) -> None:
    instrumentation = _instrumentation()
    voice_module = importlib.import_module("artmach_assistant.core.voice_service")
    voice_type = voice_module.VoiceService
    events: list[dict[str, object]] = []
    audio = tmp_path / "nested.wav"
    audio.write_bytes(b"RIFF" + b"\0" * 64)

    monkeypatch.setattr(voice_type, "record_utterance_wav", lambda self, *a, **k: audio)
    monkeypatch.setattr(voice_type, "recognize_wav", lambda self, *a, **k: "merhaba")
    instrumentation.configure_runtime_instrumentation(
        lambda **payload: events.append(payload) or True,
        workspace_provider=lambda: tmp_path,
    )
    instrumentation.install_runtime_instrumentation()

    result = voice_type().listen_utterance(device_index=3, max_seconds=2.0, model_size="small")
    assert result == "merhaba"

    nested = [
        event
        for event in events
        if event["action"] in {"audio_capture", "stt_transcription", "speech_turn"}
    ]
    assert {event["action"] for event in nested} == {
        "audio_capture",
        "stt_transcription",
        "speech_turn",
    }
    assert len({event["correlation_id"] for event in nested}) == 1
    assert "merhaba" not in repr(nested)


def test_low_level_tts_metadata_matches_backend_signatures(monkeypatch, tmp_path: Path) -> None:
    instrumentation = _instrumentation()
    voice_module = importlib.import_module("artmach_assistant.core.voice_service")
    voice_type = voice_module.VoiceService
    events: list[dict[str, object]] = []

    monkeypatch.setattr(voice_type, "_speak_with_piper", lambda self, *a, **k: "Piper tamamlandı")
    monkeypatch.setattr(voice_type, "_speak_with_windows", lambda self, *a, **k: "Windows tamamlandı")
    instrumentation.configure_runtime_instrumentation(
        lambda **payload: events.append(payload) or True,
        workspace_provider=lambda: tmp_path,
    )
    instrumentation.install_runtime_instrumentation()

    voice = voice_type()
    assert voice._speak_with_piper("gizli metin", "piper.exe", "voice.onnx", 7) == "Piper tamamlandı"
    assert voice._speak_with_windows("başka gizli metin", "Ayşe", 0, 100) == "Windows tamamlandı"

    piper = next(event for event in events if event["action"] == "tts_piper")
    windows = next(event for event in events if event["action"] == "tts_windows")
    assert piper["metadata"]["backend"] == "piper"
    assert piper["metadata"]["output_device"] == 7
    assert windows["metadata"]["backend"] == "windows"
    assert windows["metadata"]["voice_configured"] is True
    assert "gizli metin" not in repr(events)
    assert "başka gizli metin" not in repr(events)


def test_backup_batch_research_and_agent_task_outcomes_use_real_fields(monkeypatch, tmp_path: Path) -> None:
    instrumentation = _instrumentation()
    backup_module = importlib.import_module("artmach_assistant.core.project_backup_service")
    research_module = importlib.import_module("artmach_assistant.core.research_manager")
    agent_module = importlib.import_module("artmach_assistant.core.agent_task_runtime")
    backup_type = backup_module.ProjectBackupService
    research_type = research_module.ResearchManager
    agent_type = agent_module.AgentTaskRuntime
    events: list[dict[str, object]] = []

    backup_path = tmp_path / "backup"
    archive_path = tmp_path / "backup.zip"
    result = backup_module.BackupResult(
        success=True,
        backup_path=backup_path,
        file_count=4,
        total_bytes=512,
        manifest_path=backup_path / "manifest.json",
        archive_path=archive_path,
        verified=True,
    )
    monkeypatch.setattr(backup_type, "create_backup", lambda self, *a, **k: result)

    source = research_module.ResearchSource("Docs", "https://example.com", "summary")
    batch = [
        research_module.ResearchResult("one", [source]),
        research_module.ResearchResult("two", [source, source]),
    ]
    monkeypatch.setattr(research_type, "search_many", lambda self, *a, **k: batch)

    monkeypatch.setattr(agent_type, "_execute", lambda self, task_id: "done")
    monkeypatch.setattr(
        agent_type,
        "status",
        lambda self, task_id: SimpleNamespace(
            state="succeeded",
            tool_name="filesystem.copy",
            operation_id="operation-1",
        ),
    )

    instrumentation.configure_runtime_instrumentation(
        lambda **payload: events.append(payload) or True,
        workspace_provider=lambda: tmp_path,
    )
    instrumentation.install_runtime_instrumentation()

    assert backup_type().create_backup(tmp_path / "source", tmp_path / "dest") is result
    assert research_type().search_many(["one", "two"], max_results_per_query=3) == batch
    agent = agent_type.__new__(agent_type)
    assert agent._execute("task-1") == "done"

    by_action = {event["action"]: event for event in events}
    backup_metadata = by_action["create_backup"]["metadata"]
    assert backup_metadata["backup_name"] == backup_path.name
    assert backup_metadata["archive_name"] == archive_path.name
    assert str(tmp_path) not in repr(backup_metadata)
    assert by_action["web_search_batch"]["metadata"]["query_count"] == 2
    assert by_action["web_search_batch"]["metadata"]["result_count"] == 2
    assert by_action["web_search_batch"]["metadata"]["source_count"] == 3
    assert by_action["execute_tool"]["status"] == "completed"
    assert by_action["execute_tool"]["metadata"]["tool_name"] == "filesystem.copy"
