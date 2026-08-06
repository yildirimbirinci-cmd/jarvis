from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def test_runtime_research_follow_up_promotes_last_plan() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    finding = SimpleNamespace(finding_id="RUN-06578E9EDE")
    engine.last_action_context = {
        "kind": "runtime_research_plan",
        "finding_id": finding.finding_id,
    }
    engine._find_runtime_finding = lambda finding_id: (
        finding if finding_id == finding.finding_id else None
    )
    calls: list[tuple[object, bool]] = []

    def research_plan(value, *, promote_external=False):
        calls.append((value, promote_external))
        return "DIS ARASTIRMA ONAYI"

    engine._runtime_finding_research_plan = research_plan

    result = engine._runtime_research_follow_up_request(
        "Yerel inceleme tamamlandi fakat kanit kok nedeni aciklamak "
        "icin yetersiz. Dis arastirma onayi hazirla."
    )

    assert result == "DIS ARASTIRMA ONAYI"
    assert calls == [(finding, True)]


def test_runtime_research_follow_up_does_not_capture_without_context() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.last_action_context = {"kind": "collaborative_problem"}

    assert (
        engine._runtime_research_follow_up_request(
            "Dis arastirma onayi hazirla."
        )
        is None
    )



def test_runtime_research_follow_up_uses_dedicated_context_after_ui_overwrite() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    finding = SimpleNamespace(finding_id="RUN-06578E9EDE")
    engine.active_runtime_research_context = {
        "kind": "runtime_research_plan",
        "finding_id": finding.finding_id,
    }
    engine.last_action_context = {
        "kind": "maintenance_notice",
        "finding_id": "RUN-OTHER",
    }
    engine._find_runtime_finding = lambda finding_id: (
        finding if finding_id == finding.finding_id else None
    )
    calls: list[tuple[object, bool]] = []

    def research_plan(value, *, promote_external=False):
        calls.append((value, promote_external))
        return "DIS ARASTIRMA ONAYI"

    engine._runtime_finding_research_plan = research_plan

    result = engine._runtime_research_follow_up_request(
        "Yerel inceleme tamamlandi fakat kanit kok nedeni aciklamak "
        "icin yetersiz. Dis arastirma onayi hazirla."
    )

    assert result == "DIS ARASTIRMA ONAYI"
    assert calls == [(finding, True)]
