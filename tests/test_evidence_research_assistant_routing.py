from __future__ import annotations

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine


def _engine() -> AssistantEngine:
    return AssistantEngine.__new__(AssistantEngine)


def test_research_command_runs_after_retest_route() -> None:
    engine = _engine()
    calls = []

    engine._reserved_self_repair_request = (
        lambda _text: None
    )

    def retest(_text):
        calls.append("retest")
        return None

    def research(_text):
        calls.append("research")
        return "RESEARCH_HANDLED"

    engine._retest_command_request = retest
    engine._research_command_request = research

    def fail_read_only(_text):
        raise AssertionError(
            "read-only route must not run"
        )

    engine._own_code_read_only_request = fail_read_only

    result = engine.handle_local_command(
        "RS-ABCDEF1234 onayla"
    )

    assert result == "RESEARCH_HANDLED"
    assert calls == ["retest", "research"]


def test_retest_route_keeps_priority() -> None:
    engine = _engine()
    engine._reserved_self_repair_request = (
        lambda _text: None
    )
    engine._retest_command_request = (
        lambda _text: "RETEST_HANDLED"
    )

    def fail_research(_text):
        raise AssertionError(
            "research route must not run"
        )

    engine._research_command_request = fail_research
    engine._own_code_read_only_request = fail_research

    result = engine.handle_local_command(
        "RT-ABCDEF1234 onayla"
    )

    assert result == "RETEST_HANDLED"


def test_unhandled_research_allows_read_only() -> None:
    engine = _engine()
    engine._reserved_self_repair_request = (
        lambda _text: None
    )
    engine._retest_command_request = (
        lambda _text: None
    )
    engine._research_command_request = (
        lambda _text: None
    )
    engine._own_code_read_only_request = (
        lambda _text: "READ_ONLY_HANDLED"
    )

    result = engine.handle_local_command(
        "butun sistemini tara"
    )

    assert result == "READ_ONLY_HANDLED"


def test_lazy_research_coordinator_uses_persistent_store(
    monkeypatch,
    tmp_path,
) -> None:
    captured = {}

    class FakeStore:
        def __init__(self, path) -> None:
            captured["store_path"] = path

    class FakeCoordinator:
        def __init__(self, *, store) -> None:
            captured["store"] = store

        def handle(self, text):
            captured["text"] = text
            return "HANDLED"

    monkeypatch.setattr(
        assistant_module,
        "EvidenceResearchApprovalStore",
        FakeStore,
    )
    monkeypatch.setattr(
        assistant_module,
        "EvidenceResearchCommandCoordinator",
        FakeCoordinator,
    )
    monkeypatch.setattr(
        assistant_module,
        "DATA_DIR",
        tmp_path / "data",
    )

    engine = _engine()

    result = engine._research_command_request(
        "RS-ABCDEF1234 onayla"
    )

    assert result == "HANDLED"
    assert captured["store_path"] == (
        tmp_path
        / "data"
        / "diagnostics"
        / "pending_evidence_research.json"
    )
    assert captured["text"] == (
        "RS-ABCDEF1234 onayla"
    )


def test_existing_research_coordinator_is_reused() -> None:
    engine = _engine()
    calls = []

    class ExistingCoordinator:
        def handle(self, text):
            calls.append(text)
            return "EXISTING"

    engine.evidence_research_command_coordinator = (
        ExistingCoordinator()
    )

    result = engine._research_command_request(
        "RS-ABCDEF1234 onayla"
    )

    assert result == "EXISTING"
    assert calls == [
        "RS-ABCDEF1234 onayla"
    ]
