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
