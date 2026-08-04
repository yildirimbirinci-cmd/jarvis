from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine_without_active_repair() -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.editor = SimpleNamespace(pending=None)
    engine._asks_for_latest_runtime_finding = lambda _text: False
    engine._latest_runtime_finding = lambda: None
    engine._self_repair_store = lambda: SimpleNamespace(load=lambda: None)
    return engine


def test_natural_own_failure_repair_request_is_reserved() -> None:
    engine = _engine_without_active_repair()

    result = engine._reserved_self_repair_request(
        "Jarvis kendi kodundaki bu sorunu teshis et ve duzelt"
    )

    assert result is not None
    assert any(
        marker in result.casefold()
        for marker in (
            "bulgu",
            "teshis",
            "kanit",
            "onarim",
            "duzelt",
        )
    )


def test_unrelated_general_problem_does_not_enter_self_repair() -> None:
    engine = _engine_without_active_repair()

    result = engine._reserved_self_repair_request(
        "Mutfak dolabindaki bu sorunu nasil duzeltebilirim"
    )

    assert result is None

def test_inactive_old_repair_session_does_not_swallow_new_request() -> None:
    engine = _engine_without_active_repair()
    inactive_session = SimpleNamespace(
        active=False,
        state="completed",
        plan_id="RPR-OLDSESSION",
    )
    engine._self_repair_store = lambda: SimpleNamespace(
        load=lambda: inactive_session
    )

    result = engine._reserved_self_repair_request(
        "Jarvis kendi kodundaki bu sorunu teshis et ve duzelt"
    )

    assert result is not None
    assert any(
        marker in result.casefold()
        for marker in (
            "bulgu",
            "teshis",
            "kanit",
            "onar",
            "duzelt",
        )
    )

def test_natural_repair_runs_broader_diagnosis_when_runtime_finding_is_absent() -> None:
    engine = _engine_without_active_repair()
    calls: list[tuple[bool, bool]] = []

    def maintenance_review(
        *,
        own_code: bool = True,
        refresh_architecture: bool = True,
    ) -> str:
        calls.append((own_code, refresh_architecture))
        return "Bakim ve mimari teshis tamamlandi; dogrulanabilir bulgu bulunamadi."

    engine.maintenance_review = maintenance_review

    result = engine._reserved_self_repair_request(
        "Jarvis kendi kodundaki bu sorunu teshis et ve duzelt"
    )

    assert calls == [(True, True)]
    assert "teshis" in result.casefold()
    assert "bulgu" in result.casefold()

def test_latest_runtime_finding_prefers_repairable_failure_over_newer_warning() -> None:
    from artmach_assistant.core.runtime_observability import (
        RuntimeFinding,
        RuntimeHealthReport,
    )

    engine = _engine_without_active_repair()

    repairable_failure = RuntimeFinding(
        finding_id="RUN-AAAAAAAAAA",
        severity="high",
        category="repeated_runtime_failure",
        title="Tekrarlanan hata: VoiceService.speech_turn",
        explanation="Ayni hata iki kez olustu.",
        confidence=0.90,
        occurrence_count=2,
        last_seen="2026-08-04T10:00:00+00:00",
        workspace="C:/Users/yildi/Desktop/artmach_assistant",
        scope="own_code",
        affected_paths=("core/voice_service.py",),
        affected_symbols=("VoiceService.speech_turn",),
        evidence=(),
        recommendation="En kucuk kaynak duzeltmesini hazirla.",
        acceptance_criteria=("Hata tekrar olusmamali.",),
        research_query="",
    )
    newer_warning = RuntimeFinding(
        finding_id="RUN-BBBBBBBBBB",
        severity="low",
        category="repeated_runtime_warning",
        title="Tekrarlanan uyari: LocalDialogueManager.intent_model",
        explanation="Geri donus yolu kullanildi.",
        confidence=0.80,
        occurrence_count=4,
        last_seen="2026-08-04T11:00:00+00:00",
        workspace="C:/Users/yildi/Desktop/artmach_assistant",
        scope="own_code",
        affected_paths=("core/local_dialogue.py",),
        affected_symbols=("LocalDialogueManager.intent_model",),
        evidence=(),
        recommendation="Uyariyi incele.",
        acceptance_criteria=("Uyari azaltilmali.",),
        research_query="",
    )
    report = RuntimeHealthReport(
        generated_at="2026-08-04T11:01:00+00:00",
        workspace="C:/Users/yildi/Desktop/artmach_assistant",
        lookback_hours=168,
        event_count=6,
        completed_count=0,
        failed_count=2,
        cancelled_count=0,
        warning_count=4,
        findings=(repairable_failure, newer_warning),
    )
    engine._last_runtime_health_report = report
    engine._runtime_health_service = lambda: SimpleNamespace(
        analyze=lambda **_kwargs: report
    )
    engine._development_root = lambda *, own_code: Path(
        "C:/Users/yildi/Desktop/artmach_assistant"
    )

    selected = AssistantEngine._latest_runtime_finding(engine)

    assert selected is not None
    assert selected.finding_id == repairable_failure.finding_id

def test_latest_runtime_finding_prefers_source_target_over_untargeted_repeat() -> None:
    from pathlib import Path
    from artmach_assistant.core.runtime_observability import (
        RuntimeFinding,
        RuntimeHealthReport,
    )

    engine = _engine_without_active_repair()

    untargeted_repeat = RuntimeFinding(
        finding_id="RUN-CCCCCCCCCC",
        severity="critical",
        category="repeated_runtime_failure",
        title="Tekrarlanan fakat hedeflenemeyen hata",
        explanation="Hata tekrarlandi ancak kaynak baglantisi yok.",
        confidence=0.99,
        occurrence_count=8,
        last_seen="2026-08-04T12:00:00+00:00",
        workspace="C:/Users/yildi/Desktop/artmach_assistant",
        scope="own_code",
        affected_paths=(),
        affected_symbols=(),
        evidence=(),
        recommendation="Kaynak baglantisini bul.",
        acceptance_criteria=("Hata tekrar olusmamali.",),
        research_query="",
    )
    targeted_failure = RuntimeFinding(
        finding_id="RUN-DDDDDDDDDD",
        severity="high",
        category="runtime_failure",
        title="Hedeflenebilir VoiceService hatasi",
        explanation="Dosya ve sembol baglantisi dogrulandi.",
        confidence=0.90,
        occurrence_count=1,
        last_seen="2026-08-04T11:00:00+00:00",
        workspace="C:/Users/yildi/Desktop/artmach_assistant",
        scope="own_code",
        affected_paths=("core/voice_service.py",),
        affected_symbols=("VoiceService.speech_turn",),
        evidence=(),
        recommendation="Hedefli kaynak duzeltmesi hazirla.",
        acceptance_criteria=("Hata tekrar olusmamali.",),
        research_query="",
    )
    report = RuntimeHealthReport(
        generated_at="2026-08-04T12:01:00+00:00",
        workspace="C:/Users/yildi/Desktop/artmach_assistant",
        lookback_hours=168,
        event_count=9,
        completed_count=0,
        failed_count=9,
        cancelled_count=0,
        warning_count=0,
        findings=(untargeted_repeat, targeted_failure),
    )
    engine._last_runtime_health_report = report
    engine._runtime_health_service = lambda: SimpleNamespace(
        analyze=lambda **_kwargs: report
    )
    engine._development_root = lambda *, own_code: Path(
        "C:/Users/yildi/Desktop/artmach_assistant"
    )

    selected = AssistantEngine._latest_runtime_finding(engine)

    assert selected is not None
    assert selected.finding_id == targeted_failure.finding_id
