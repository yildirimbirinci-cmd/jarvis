from __future__ import annotations

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine


def _engine() -> AssistantEngine:
    return AssistantEngine.__new__(AssistantEngine)


def test_retest_command_precedes_read_only_review() -> None:
    engine = _engine()
    engine._reserved_self_repair_request = (
        lambda _text: None
    )
    engine._retest_command_request = (
        lambda _text: "RETEST_HANDLED"
    )

    def fail_read_only(_text):
        raise AssertionError(
            "read-only routing must not run first"
        )

    engine._own_code_read_only_request = fail_read_only

    result = engine.handle_local_command(
        "yeniden test edilmesi gereken "
        "bulgulari dogrula"
    )

    assert result == "RETEST_HANDLED"


def test_unhandled_retest_allows_read_only_review() -> None:
    engine = _engine()
    engine._reserved_self_repair_request = (
        lambda _text: None
    )
    engine._retest_command_request = (
        lambda _text: None
    )
    engine._own_code_read_only_request = (
        lambda _text: "READ_ONLY_HANDLED"
    )

    result = engine.handle_local_command(
        "butun sistemini tara ve raporla"
    )

    assert result == "READ_ONLY_HANDLED"


def test_lazy_coordinator_uses_persistent_store(
    monkeypatch,
    tmp_path,
) -> None:
    captured = {}

    class FakeStore:
        def __init__(self, path) -> None:
            captured["store_path"] = path

    class FakeCoordinator:
        def __init__(
            self,
            *,
            store,
            source_root,
            plan_provider,
            result_handler=None,
            completion_store=None,
        ) -> None:
            captured["store"] = store
            captured["source_root"] = source_root
            captured["plan_provider"] = plan_provider
            captured["result_handler"] = result_handler
            captured["completion_store"] = completion_store

        def handle(self, text):
            captured["text"] = text
            return "HANDLED"

    monkeypatch.setattr(
        assistant_module,
        "RetestApprovalStore",
        FakeStore,
    )
    monkeypatch.setattr(
        assistant_module,
        "RetestCommandCoordinator",
        FakeCoordinator,
    )
    monkeypatch.setattr(
        assistant_module,
        "DATA_DIR",
        tmp_path / "data",
    )

    engine = _engine()
    engine.own_project_root = lambda: tmp_path
    engine._build_evidence_retest_plan = (
        lambda: None
    )

    result = engine._retest_command_request(
        "retest planini baslat"
    )

    assert result == "HANDLED"
    assert captured["source_root"] == tmp_path
    assert captured["store_path"] == (
        tmp_path
        / "data"
        / "diagnostics"
        / "pending_retest.json"
    )


def test_existing_coordinator_is_reused() -> None:
    engine = _engine()
    calls = []

    class ExistingCoordinator:
        def handle(self, text):
            calls.append(text)
            return "EXISTING"

    engine.retest_command_coordinator = (
        ExistingCoordinator()
    )

    result = engine._retest_command_request(
        "RT-ABCDEF1234 onayla"
    )

    assert result == "EXISTING"
    assert calls == [
        "RT-ABCDEF1234 onayla"
    ]
