from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine


def _engine(monkeypatch):
    engine = AssistantEngine.__new__(AssistantEngine)
    monkeypatch.setattr(engine, "normalize_address", lambda text: text)
    monkeypatch.setattr(
        engine,
        "_asks_for_engineering_state_only",
        lambda text: False,
    )
    monkeypatch.setattr(
        engine,
        "_patch_session_command_request",
        lambda text: None,
    )
    monkeypatch.setattr(
        engine,
        "_retest_command_request",
        lambda text: "RETEST-ROUTED",
    )

    def forbidden_reserved(text):
        raise AssertionError(
            "explicit retest command reached generic self-repair routing"
        )

    monkeypatch.setattr(
        engine,
        "_reserved_self_repair_request",
        forbidden_reserved,
    )
    return engine


def test_saved_revalidation_plan_routes_to_retest_coordinator(monkeypatch) -> None:
    engine = _engine(monkeypatch)

    result = engine.handle_local_command(
        "Kayitli yeniden dogrulama planini goster."
    )

    assert result == "RETEST-ROUTED"


def test_primary_revalidation_execution_routes_before_generic_repair(
    monkeypatch,
) -> None:
    engine = _engine(monkeypatch)

    result = engine.handle_local_command(
        "TaskOrchestrator.execute_task icin kayitli primary "
        "yeniden dogrulama testlerini simdi calistir. "
        "Kod degistirme, patch hazirlama, plan uretme."
    )

    assert result == "RETEST-ROUTED"


def test_saved_plan_primary_tests_execution_routes_to_retest(
    monkeypatch,
) -> None:
    engine = _engine(monkeypatch)

    result = engine.handle_local_command(
        "Kayitli yeniden dogrulama planindaki "
        "TaskOrchestrator.execute_task primary testlerini calistir."
    )

    assert result == "RETEST-ROUTED"


def test_retest_routing_contract_tokens_exist() -> None:
    source = open("core/assistant.py", encoding="utf-8").read()

    assert '"yeniden dogrulama"' in source
    assert '"primary test"' in source
    assert '"birincil test"' in source
    assert '"calistir"' in source
    assert '"goster"' in source
