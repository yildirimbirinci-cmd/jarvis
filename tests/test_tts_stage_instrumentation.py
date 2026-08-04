from __future__ import annotations

from pathlib import Path

import importlib

from artmach_assistant.core.voice_service import VoiceService


def test_voice_stage_helper_emits_bounded_runtime_event(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    runtime_instrumentation = importlib.import_module(
        "artmach_assistant.core.runtime_instrumentation"
    )

    monkeypatch.setattr(
        runtime_instrumentation,
        "record_runtime_stage",
        lambda **kwargs: captured.append(dict(kwargs)) or True,
    )

    VoiceService._record_tts_stage(
        "tts_cache_lookup",
        0.0,
        session_id="speech-test",
        metadata={"cache_hit": True},
    )

    assert len(captured) == 1
    row = captured[0]
    assert row["component"] == "VoiceService"
    assert row["action"] == "tts_cache_lookup"
    assert row["status"] == "completed"
    assert row["scope"] == "voice"
    assert row["correlation_id"] == "speech-test"
    assert row["metadata"]["cache_hit"] is True


def test_voice_stage_helper_is_fail_open(monkeypatch) -> None:
    runtime_instrumentation = importlib.import_module(
        "artmach_assistant.core.runtime_instrumentation"
    )

    monkeypatch.setattr(
        runtime_instrumentation,
        "record_runtime_stage",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("store failed")),
    )

    VoiceService._record_tts_stage(
        "tts_piper_synthesis",
        0.0,
        session_id="speech-test",
    )


def test_piper_pipeline_records_expected_internal_stages() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "core"
        / "voice_service.py"
    ).read_text(encoding="utf-8")

    expected = (
        '"tts_cache_lookup"',
        '"tts_piper_synthesis"',
        '"tts_wav_decode"',
        '"tts_audio_prepare"',
    )

    for action in expected:
        assert action in source


def test_audio_playback_records_expected_internal_stages() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "core"
        / "voice_service.py"
    ).read_text(encoding="utf-8")

    expected = (
        '"tts_stream_open"',
        '"tts_first_audio_write"',
        '"tts_playback_complete"',
    )

    for action in expected:
        assert action in source


def test_piper_wait_boundaries_are_instrumented() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "core"
        / "voice_service.py"
    ).read_text(encoding="utf-8")

    assert '"tts_piper_discovery"' in source
    assert '"tts_piper_chunk_ready"' in source

    discovery_start = source.index("discovery_started = time.perf_counter()")
    discovery_call = source.index(
        "self._discover_piper(executable, model_path)",
        discovery_start,
    )
    discovery_event = source.index(
        '"tts_piper_discovery"',
        discovery_call,
    )
    assert discovery_start < discovery_call < discovery_event

    chunk_start = source.index("chunk_started = time.perf_counter()")
    cache_lookup = source.index('"tts_cache_lookup"', chunk_start)
    chunk_ready = source.index('"tts_piper_chunk_ready"', cache_lookup)
    assert chunk_start < cache_lookup < chunk_ready
