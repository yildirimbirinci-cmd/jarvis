from artmach_assistant.core.code_research_target_resolver import resolve_code_target
from artmach_assistant.core.evidence_maintenance import EvidenceMaintenanceFinding
from artmach_assistant.core.evidence_research import BLOCKED, build_evidence_research_plan


def _finding(*, title="runtime problem", path="", symbol=""):
    return EvidenceMaintenanceFinding(
        classification="A",
        score=95,
        source="runtime",
        title=title,
        finding_id="RUN-GENERIC",
        path=path,
        symbol=symbol,
        lifecycle="ACTIVE",
    )


def test_structured_finding_target_is_used_without_title_parsing():
    resolution = resolve_code_target(
        _finding(path="core/example.py", symbol="Example.run")
    )
    assert resolution.target.path == "core/example.py"
    assert resolution.target.symbol == "Example.run"
    assert resolution.source == "finding"


def test_promoted_runtime_target_has_priority():
    resolution = resolve_code_target(
        _finding(path="core/wrapper.py", symbol="Wrapper.run"),
        promoted_path="core/action.py",
        promoted_symbol="Action.execute",
    )
    assert resolution.target.path == "core/action.py"
    assert resolution.target.symbol == "Action.execute"
    assert resolution.source == "promoted_runtime_target"


def test_issue_title_cannot_invent_a_target():
    resolution = resolve_code_target(
        _finding(title="TaskOrchestrator.execute_task repeated slow operation")
    )
    assert resolution.resolved is False


def test_unresolved_research_target_is_blocked_instead_of_guessed():
    plan = build_evidence_research_plan(
        _finding(title="TaskOrchestrator.execute_task repeated slow operation")
    )
    assert plan.status == BLOCKED
    assert plan.path == ""
    assert plan.symbol == ""


def test_arbitrary_structured_target_reaches_normal_research_plan():
    plan = build_evidence_research_plan(
        _finding(path="plugins/adapter.py", symbol="Adapter.execute")
    )
    assert plan.status != BLOCKED
    assert plan.path == "plugins/adapter.py"
    assert plan.symbol == "Adapter.execute"
