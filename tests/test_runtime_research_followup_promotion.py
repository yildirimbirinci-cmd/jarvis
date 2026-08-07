from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine


def _engine() -> AssistantEngine:
    engine = object.__new__(AssistantEngine)
    engine.active_runtime_research_context = {
        "kind": "runtime_research_plan",
        "finding_id": "RUN-06578E9EDE",
    }
    engine.last_action_context = {}
    finding = SimpleNamespace(finding_id="RUN-06578E9EDE")
    engine._find_runtime_finding = lambda finding_id: (
        finding if finding_id == "RUN-06578E9EDE" else None
    )
    engine._runtime_finding_research_plan = (
        lambda selected, *, promote_external=False: (
            f"PROMOTE:{selected.finding_id}:{promote_external}"
        )
    )
    return engine


def test_natural_insufficient_local_evidence_promotes_active_runtime_research() -> None:
    engine = _engine()

    result = engine._runtime_research_follow_up_request(
        "Yerel inceleme bu bulgu icin yeterli kanit saglamadi. "
        "Harici arastirma gerekiyorsa onay akisini hazirla."
    )

    assert result == "PROMOTE:RUN-06578E9EDE:True"


def test_unrelated_followup_does_not_promote_runtime_research() -> None:
    engine = _engine()

    result = engine._runtime_research_follow_up_request(
        "Yerel incelemeye devam et."
    )

    assert result is None
