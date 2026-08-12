from artmach_assistant.core.code_research_contracts import (
    CodeEvidenceState,
    CodeResearchAction,
    CodeTarget,
)
from artmach_assistant.core.code_research_pipeline import (
    decide_code_research,
    patch_may_be_generated,
)


def test_missing_target_blocks_research_chain():
    decision = decide_code_research(CodeTarget(""), CodeEvidenceState())
    assert decision.action is CodeResearchAction.BLOCKED


def test_real_source_and_tests_are_required_before_external_research():
    target = CodeTarget("core/assistant.py", "AssistantEngine.handle")
    decision = decide_code_research(target, CodeEvidenceState(source_seen=True))
    assert decision.action is CodeResearchAction.LOCAL_REVIEW


def test_local_root_cause_goes_to_plan_without_external_research():
    target = CodeTarget("core/task_orchestrator.py", "TaskOrchestrator.execute_task")
    evidence = CodeEvidenceState(
        source_seen=True,
        tests_seen=True,
        runtime_evidence_seen=True,
        local_root_cause_supported=True,
    )
    decision = decide_code_research(target, evidence)
    assert decision.action is CodeResearchAction.READY_FOR_PLAN
    assert patch_may_be_generated(target, evidence) is True


def test_insufficient_local_evidence_promotes_general_external_research():
    target = CodeTarget("core/example.py", "Example.run")
    evidence = CodeEvidenceState(source_seen=True, tests_seen=True)
    decision = decide_code_research(target, evidence)
    assert decision.action is CodeResearchAction.EXTERNAL_RESEARCH
    assert patch_may_be_generated(target, evidence) is False


def test_external_evidence_never_skips_local_source_and_tests():
    target = CodeTarget("core/example.py", "Example.run")
    evidence = CodeEvidenceState(
        external_research_requested=True,
        external_evidence_seen=True,
    )
    decision = decide_code_research(target, evidence)
    assert decision.action is CodeResearchAction.LOCAL_REVIEW
    assert patch_may_be_generated(target, evidence) is False


def test_external_evidence_can_unlock_plan_only_after_local_contract_review():
    target = CodeTarget("core/example.py", "Example.run")
    evidence = CodeEvidenceState(
        source_seen=True,
        tests_seen=True,
        external_research_requested=True,
        external_evidence_seen=True,
    )
    decision = decide_code_research(target, evidence)
    assert decision.action is CodeResearchAction.READY_FOR_PLAN
    assert patch_may_be_generated(target, evidence) is True


def test_pipeline_is_not_bound_to_known_issue_names():
    targets = (
        CodeTarget("core/a.py", "A.run"),
        CodeTarget("plugins/x.py", "X.execute"),
        CodeTarget("services/y.py", "Y.handle"),
    )
    evidence = CodeEvidenceState(source_seen=True, tests_seen=True)
    assert all(
        decide_code_research(target, evidence).action
        is CodeResearchAction.EXTERNAL_RESEARCH
        for target in targets
    )
