from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.evidence_patch_closeout import (
    CLOSEOUT_COMPLETED,
    CLOSEOUT_PENDING,
    run_patch_closeout,
    select_patch_retest_item,
)
from artmach_assistant.core.evidence_patch_session import (
    SESSION_APPLIED,
    SESSION_APPLYING,
    SESSION_APPROVED,
    SESSION_APPROVAL_PENDING,
    SESSION_EDIT_PROPOSAL_READY,
    SESSION_HANDOFF_READY,
    SESSION_VALIDATION_PENDING,
    EvidencePatchSession,
)
from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
    RetestPlan,
)
from artmach_assistant.core.evidence_retest_completion import (
    RetestCompletionStore,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_PASSED,
    RetestExecutionResult,
)


def _applied_session() -> EvidencePatchSession:
    session = EvidencePatchSession.create(
        proposal_id="PP-CLOSEOUT",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )
    for status in (
        SESSION_HANDOFF_READY,
        SESSION_EDIT_PROPOSAL_READY,
        SESSION_VALIDATION_PENDING,
        SESSION_APPROVAL_PENDING,
        SESSION_APPROVED,
        SESSION_APPLYING,
        SESSION_APPLIED,
    ):
        session = session.transition(status)
    return session


def _item() -> RetestItem:
    return RetestItem(
        title="Repeated slow operation",
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.wrap.execute",
        status=AUTOMATED,
        primary_test_paths=(
            "tests/test_task_orchestrator_stage_timing.py",
        ),
        test_paths=(
            "tests/test_task_orchestrator_stage_timing.py",
        ),
        command=(
            "python",
            "-m",
            "pytest",
            "tests/test_task_orchestrator_stage_timing.py",
            "-q",
        ),
        reason="Exact target test.",
    )


def test_select_patch_retest_item_prefers_exact_target() -> None:
    expected = _item()
    other = RetestItem(
        title="Other",
        path="core/task_orchestrator.py",
        symbol="TaskOrchestrator.other",
        status=AUTOMATED,
    )

    selected = select_patch_retest_item(
        RetestPlan((other, expected)),
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )

    assert selected == expected


def test_passed_closeout_records_completion(tmp_path: Path) -> None:
    store = RetestCompletionStore(
        tmp_path / "completed_retests.json"
    )

    def executor(item, *, source_root):
        assert item == _item()
        assert Path(source_root) == tmp_path
        return RetestExecutionResult(
            status=RETEST_PASSED,
            title=item.title,
            path=item.path,
            symbol=item.symbol,
            command=item.command,
            returncode=0,
            duration_ms=12.5,
            reason="Primary yeniden testler gecti.",
        )

    outcome = run_patch_closeout(
        _applied_session(),
        RetestPlan((_item(),)),
        source_root=tmp_path,
        completion_store=store,
        executor=executor,
    )

    assert outcome.status == CLOSEOUT_COMPLETED
    assert outcome.completed is True
    records = store.load()
    assert len(records) == 1
    assert records[0].status == RETEST_PASSED
    assert records[0].path == "core/task_orchestrator.py"


def test_missing_target_keeps_closeout_pending(tmp_path: Path) -> None:
    outcome = run_patch_closeout(
        _applied_session(),
        RetestPlan(()),
        source_root=tmp_path,
        completion_store=RetestCompletionStore(
            tmp_path / "completed_retests.json"
        ),
    )

    assert outcome.status == CLOSEOUT_PENDING
    assert outcome.completed is False
