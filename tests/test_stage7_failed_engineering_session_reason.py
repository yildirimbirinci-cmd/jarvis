from __future__ import annotations

from dataclasses import replace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_patch_session import EvidencePatchSession, SESSION_FAILED


def _engine(session):
    engine = object.__new__(AssistantEngine)
    engine.command_key = lambda text: " ".join(str(text).casefold().split())
    engine._evidence_patch_session_store = lambda: type(
        "Store", (), {"load": lambda self: session}
    )()
    return engine


def _failed_session():
    session = EvidencePatchSession.create(
        proposal_id="proposal",
        target_path="core/assistant.py",
        target_symbol="AssistantEngine.handle",
    )
    return session.transition(
        SESSION_FAILED,
        error="SOURCE GROUNDED ANCHOR REDDI",
        validation_summary="validator rejected live-source anchor",
    )


def test_failed_session_reason_reads_persistent_failure_details():
    result = _engine(_failed_session())._failed_engineering_session_reason_request(
        "En son basarisiz kod gelistirme oturumunun neden basarisiz oldugunu "
        "kalici engineering kayitlarindan acikla. Yalniz gercek kayitlari kullan. "
        "Plan, patch veya kod degisikligi baslatma."
    )
    assert result is not None
    assert "BASARISIZ ENGINEERING OTURUMU" in result
    assert "SOURCE GROUNDED ANCHOR REDDI" in result
    assert "validator rejected live-source anchor" in result
    assert "KAYITLI ENGINEERING DURUMU" not in result


def test_generic_engineering_status_is_not_stolen():
    result = _engine(_failed_session())._failed_engineering_session_reason_request(
        "Kayitli engineering durumunu goster."
    )
    assert result is None


def test_non_failed_session_is_reported_without_inventing_reason():
    active = EvidencePatchSession.create(
        proposal_id="proposal",
        target_path="core/assistant.py",
        target_symbol="AssistantEngine.handle",
    )
    result = _engine(active)._failed_engineering_session_reason_request(
        "Son basarisiz engineering oturumu neden basarisiz oldu? Acikla."
    )
    assert result is not None
    assert "FAILED durumunda bir oturum yok" in result
