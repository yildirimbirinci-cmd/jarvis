from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.voice_diagnostic_session import (
    VoiceDiagnosticSession,
)


def event(
    event_id: str,
    *,
    component: str = "VoiceService",
    action: str,
    status: str = "success",
    duration_ms: float = 0.0,
    error_type: str = "",
):
    return SimpleNamespace(
        event_id=event_id,
        component=component,
        action=action,
        status=status,
        duration_ms=duration_ms,
        error_type=error_type,
        message="",
    )


def test_session_ignores_all_preexisting_events() -> None:
    old = event(
        "old",
        action="audio_capture",
        status="failed",
        error_type="TypeError",
    )
    session = VoiceDiagnosticSession.start(
        [old],
        session_id="VDG-TEST",
    )

    report = session.finish([old])

    assert report.event_count == 0
    assert report.failures == ()


def test_session_reports_only_new_voice_stage_events() -> None:
    old = event("old", action="tts_piper", duration_ms=46000)
    new_capture = event(
        "new-1",
        action="audio_capture",
        duration_ms=1200,
    )
    new_tts = event(
        "new-2",
        action="tts_piper",
        duration_ms=2400,
    )
    unrelated = event(
        "new-3",
        component="TaskOrchestrator",
        action="execute_task",
        duration_ms=9000,
    )

    session = VoiceDiagnosticSession.start(
        [old],
        session_id="VDG-TEST",
    )
    report = session.finish(
        [old, new_capture, new_tts, unrelated]
    )

    assert report.event_count == 2
    assert report.stage_durations["mikrofon kaydi"] == (1200.0,)
    assert report.stage_durations["Piper hazirlama ve oynatma"] == (
        2400.0,
    )
