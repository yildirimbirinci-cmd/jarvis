from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.voice_diagnostic_session import (
    VoiceDiagnosticResult,
)


def engine_with_result() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine._last_voice_diagnostic_result = VoiceDiagnosticResult(
        session_id="VDG-PLAN",
        event_count=12,
        stage_durations={
            "TTS yonlendirme": (9600.0, 9700.0),
            "Piper hazirlama ve oynatma": (5100.0, 5300.0),
            "transkripsiyon": (1200.0,),
        },
    )
    return engine


def test_plan_request_uses_last_voice_diagnostic_result() -> None:
    engine = engine_with_result()

    answer = engine._voice_diagnostic_request(
        "Bu yeni ses tanilamasina gore en dusuk riskli "
        "duzeltme planini ve test planini hazirla. "
        "Yalniz TTS ve Piper gecikmesine odaklan."
    )

    assert answer is not None
    assert "VDG-PLAN" in answer
    assert "TTS yonlendirme" in answer
    assert "core/voice_service.py" in answer
    assert "Henuz" not in answer
    assert "Hen\u00fcz hi\u00e7bir kaynak dosya" in answer


def test_generic_source_history_request_is_not_claimed_without_voice_context() -> None:
    engine = engine_with_result()

    answer = engine._voice_diagnostic_request(
        "Kendi kaynak islem gecmisini goster."
    )

    assert answer is None
