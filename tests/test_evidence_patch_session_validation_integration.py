from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.edit_manager import (
    EditProposal,
    ProposedFileChange,
)
from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPROVAL_PENDING,
    SESSION_APPROVED,
    SESSION_EDIT_PROPOSAL_READY,
    SESSION_HANDOFF_READY,
    EvidencePatchSession,
    EvidencePatchSessionStore,
)
from artmach_assistant.core.own_code_worktree import (
    WorktreeValidationResult,
)


def _assistant_module():
    return importlib.import_module(
        "artmach_assistant.core.assistant"
    )


def _engine(tmp_path: Path):
    AssistantEngine = _assistant_module().AssistantEngine
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.own_project_root = lambda: tmp_path
    proposal = EditProposal(
        summary="safe proposal",
        files=[
            ProposedFileChange(
                path="core/task_orchestrator.py",
                reason="validation integration test",
                old_content="pass\n",
                new_content="pass\n# measured\n",
                existed=True,
            ),
        ],
    )
    engine.editor = SimpleNamespace(
        pending=proposal,
        reject=lambda: None,
    )
    engine._run_own_tests = lambda: (
        True,
        "baseline passed",
    )
    engine._test_failure_ids = lambda output: set()
    engine._validate_own_code_at_root = (
        lambda root, baseline_failures=None: (
            True,
            "focused tests passed",
        )
    )
    return engine


def _store_ready_session(
    engine,
    tmp_path: Path,
) -> EvidencePatchSession:
    store = EvidencePatchSessionStore(
        tmp_path
        / ".jarvis"
        / "evidence_patch_session.json"
    )
    session = EvidencePatchSession.create(
        proposal_id="PP-VALIDATE",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )
    session = session.transition(SESSION_HANDOFF_READY)
    session = session.transition(
        SESSION_EDIT_PROPOSAL_READY
    )
    store.save(session)
    return session


class _PassingValidator:
    def __init__(self, _root: Path) -> None:
        pass

    def validate(self, proposal, runner):
        assert getattr(proposal, "files", None)
        return WorktreeValidationResult(
            True,
            "isolated validation passed",
        )


def _install_passing_validator(monkeypatch) -> None:
    monkeypatch.setattr(
        _assistant_module(),
        "OwnCodeWorktreeValidator",
        _PassingValidator,
    )


def test_validation_moves_session_to_approval_pending(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = (
        tmp_path
        / "core"
        / "task_orchestrator.py"
    )
    target.parent.mkdir(parents=True)
    target.write_text("pass\n", encoding="utf-8")
    engine = _engine(tmp_path)
    _store_ready_session(engine, tmp_path)
    _install_passing_validator(monkeypatch)

    rendered = (
        engine.validate_evidence_patch_session()
    )
    store = engine._evidence_patch_session_store()
    session = store.load()

    assert session is not None, rendered
    assert session.status == SESSION_APPROVAL_PENDING, (
        rendered
    )
    assert session.apply_allowed is False
    assert (
        "isolated validation passed"
        in session.worktree_summary
    )
    assert "APPROVAL_PENDING" in rendered


def test_explicit_session_id_is_required_for_approval(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    session = _store_ready_session(
        engine,
        tmp_path,
    )
    store = engine._evidence_patch_session_store()
    session = session.transition(
        "VALIDATION_PENDING"
    )
    session = session.transition(
        SESSION_APPROVAL_PENDING
    )
    store.save(session)

    rejected = (
        engine.approve_evidence_patch_session(
            "PS-WRONG"
        )
    )
    assert "eslesmiyor" in rejected
    assert (
        store.load().status
        == SESSION_APPROVAL_PENDING
    )

    approved = (
        engine.approve_evidence_patch_session(
            session.session_id
        )
    )
    assert "APPROVED" in approved
    assert (
        store.load().status
        == SESSION_APPROVED
    )
    assert store.load().apply_allowed is True


def test_validation_accepts_structurally_valid_reloaded_proposal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "core" / "task_orchestrator.py"
    target.parent.mkdir(parents=True)
    target.write_text("pass\n", encoding="utf-8")
    engine = _engine(tmp_path)
    original = engine.editor.pending

    class ReloadedProposal:
        def __init__(self) -> None:
            self.summary = original.summary
            self.files = original.files

    engine.editor.pending = ReloadedProposal()
    _store_ready_session(engine, tmp_path)
    _install_passing_validator(monkeypatch)

    rendered = engine.validate_evidence_patch_session()
    session = engine._evidence_patch_session_store().load()

    assert session is not None, rendered
    assert session.status == SESSION_APPROVAL_PENDING, (
        rendered
    )
    assert "APPROVAL_PENDING" in rendered
