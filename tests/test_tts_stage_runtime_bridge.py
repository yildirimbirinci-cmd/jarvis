from __future__ import annotations

import importlib


def test_tts_stage_reaches_runtime_event_store(tmp_path) -> None:
    # Some instrumentation resilience tests deliberately reload modules.
    # Resolve all cooperating classes from the currently registered module
    # objects so this integration test never uses stale references.
    instrumentation = importlib.import_module(
        "artmach_assistant.core.runtime_instrumentation"
    )
    observability = importlib.import_module(
        "artmach_assistant.core.runtime_observability"
    )
    voice_module = importlib.import_module(
        "artmach_assistant.core.voice_service"
    )

    store = observability.RuntimeEventStore(tmp_path / "events.json")

    instrumentation.configure_runtime_instrumentation(
        store.record,
        workspace_provider=lambda: str(tmp_path),
    )
    try:
        voice_module.VoiceService._record_tts_stage(
            "tts_cache_lookup",
            0.0,
            session_id="speech-integration-test",
            metadata={"cache_hit": True},
        )
        events = store.load()
    finally:
        instrumentation.configure_runtime_instrumentation(None)

    assert len(events) == 1
    event = events[0]
    assert event.component == "VoiceService"
    assert event.action == "tts_cache_lookup"
    assert event.status == "completed"
    assert event.scope == "voice"
    assert event.correlation_id == "speech-integration-test"
    assert event.metadata["cache_hit"] is True
