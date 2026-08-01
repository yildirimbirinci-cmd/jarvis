from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.runtime_observability import RuntimeEventStore, RuntimeHealthAnalyzer


def test_repeated_fallback_warning_becomes_maintenance_finding(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(3):
        store.record(
            component="VoiceService",
            action="tts_dispatch",
            status="warning",
            workspace=tmp_path,
            scope="voice",
            source_path="core/voice_service.py",
            symbol="VoiceService.speak",
            message="Windows TTS kullanıldı. Piper hatası: Invalid sample rate -9997",
        )

    report = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)
    finding = next(item for item in report.findings if item.category == "repeated_runtime_warning")
    assert finding.occurrence_count == 3
    assert finding.affected_paths == ("core/voice_service.py",)
    assert finding.affected_symbols == ("VoiceService.speak",)


def test_per_operation_slow_threshold_avoids_false_audio_findings(tmp_path: Path) -> None:
    store = RuntimeEventStore(tmp_path / "events.json")
    for _ in range(3):
        store.record(
            component="VoiceService",
            action="audio_capture",
            status="completed",
            duration_ms=6000,
            workspace=tmp_path,
            scope="voice",
            source_path="core/voice_service.py",
            symbol="VoiceService.record_utterance_wav",
            metadata={"slow_threshold_ms": 9000},
        )
        store.record(
            component="LocalDialogueManager",
            action="chat_model",
            status="completed",
            duration_ms=7000,
            workspace=tmp_path,
            scope="model",
            source_path="core/local_dialogue.py",
            symbol="LocalDialogueManager.respond",
            metadata={"slow_threshold_ms": 5000},
        )

    report = RuntimeHealthAnalyzer(store).analyze(workspace=tmp_path)
    slow_actions = {item.title for item in report.findings if item.category == "repeated_slow_operation"}
    assert not any("audio_capture" in title for title in slow_actions)
    assert any("chat_model" in title for title in slow_actions)
