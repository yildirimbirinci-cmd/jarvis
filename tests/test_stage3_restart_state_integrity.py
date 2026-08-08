from __future__ import annotations

from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.own_code_approval import proposal_fingerprint


def _proposal() -> EditProposal:
    return EditProposal(
        "integrity",
        [
            ProposedFileChange(
                path="core/example.py",
                reason="test",
                old_content="old\n",
                new_content="new\n",
                existed=True,
            )
        ],
    )


def test_resume_rejects_validation_paths_from_another_proposal() -> None:
    proposal = _proposal()
    state = SimpleNamespace(
        proposal_fingerprint=proposal_fingerprint(proposal),
        changed_paths=("core/other.py",),
        phase="validating",
    )

    class Store:
        cleared = False
        def load(self):
            return state
        def clear(self):
            self.cleared = True

    store = Store()
    engine = AssistantEngine.__new__(AssistantEngine)
    engine._own_code_validation_state_store = lambda: store
    ok, detail = engine._validate_restart_safe_resume_state(proposal)
    assert ok is False
    assert "paths do not match" in detail
    assert store.cleared is True


def test_resume_accepts_exact_validation_paths() -> None:
    proposal = _proposal()
    state = SimpleNamespace(
        proposal_fingerprint=proposal_fingerprint(proposal),
        changed_paths=("core/example.py",),
        phase="revalidating",
    )

    class Store:
        def load(self):
            return state
        def clear(self):
            raise AssertionError("matching state must not be cleared")

    engine = AssistantEngine.__new__(AssistantEngine)
    engine._own_code_validation_state_store = lambda: Store()
    ok, detail = engine._validate_restart_safe_resume_state(proposal)
    assert ok is True
    assert "accepted" in detail
