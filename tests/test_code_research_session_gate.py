import pytest

from artmach_assistant.core.code_research_contracts import CodeResearchAction, CodeTarget
from artmach_assistant.core.code_research_session import CodeResearchEvent, CodeResearchSession
from artmach_assistant.core.patch_research_gate import (
    require_patch_research_gate,
    validate_patch_research_gate,
)


def test_session_starts_with_local_review():
    session = CodeResearchSession(CodeTarget("core/example.py", "Example.run"))
    assert session.next_action is CodeResearchAction.LOCAL_REVIEW
    assert session.can_generate_patch is False


def test_source_and_tests_without_root_cause_promote_research():
    session = CodeResearchSession(CodeTarget("core/example.py", "Example.run"))
    session.record_many(
        [CodeResearchEvent.SOURCE_REVIEWED, CodeResearchEvent.TESTS_REVIEWED]
    )
    assert session.next_action is CodeResearchAction.EXTERNAL_RESEARCH
    assert validate_patch_research_gate(session).allowed is False


def test_local_root_cause_unlocks_patch_gate():
    session = CodeResearchSession(CodeTarget("core/example.py", "Example.run"))
    session.record_many(
        [
            CodeResearchEvent.SOURCE_REVIEWED,
            CodeResearchEvent.TESTS_REVIEWED,
            CodeResearchEvent.RUNTIME_EVIDENCE,
            CodeResearchEvent.LOCAL_ROOT_CAUSE,
        ]
    )
    assert session.next_action is CodeResearchAction.READY_FOR_PLAN
    assert validate_patch_research_gate(session).allowed is True
    require_patch_research_gate(session)


def test_external_research_does_not_unlock_patch_without_local_contract():
    session = CodeResearchSession(CodeTarget("core/example.py", "Example.run"))
    session.record_many(
        [
            CodeResearchEvent.EXTERNAL_REQUESTED,
            CodeResearchEvent.EXTERNAL_EVIDENCE,
        ]
    )
    assert session.can_generate_patch is False
    with pytest.raises(RuntimeError):
        require_patch_research_gate(session)


def test_external_evidence_unlocks_only_after_source_and_tests():
    session = CodeResearchSession(CodeTarget("plugins/new_adapter.py", "Adapter.execute"))
    session.record_many(
        [
            CodeResearchEvent.SOURCE_REVIEWED,
            CodeResearchEvent.TESTS_REVIEWED,
            CodeResearchEvent.EXTERNAL_REQUESTED,
            CodeResearchEvent.EXTERNAL_EVIDENCE,
        ]
    )
    assert session.next_action is CodeResearchAction.READY_FOR_PLAN
    assert session.can_generate_patch is True


def test_notes_do_not_control_decision_logic():
    session = CodeResearchSession(CodeTarget("services/arbitrary.py", "Arbitrary.call"))
    session.record(CodeResearchEvent.SOURCE_REVIEWED, "RUN-UNKNOWN arbitrary wording")
    session.record(CodeResearchEvent.TESTS_REVIEWED, "completely different issue")
    assert session.next_action is CodeResearchAction.EXTERNAL_RESEARCH
