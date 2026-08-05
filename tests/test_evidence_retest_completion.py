from __future__ import annotations

from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
)
from artmach_assistant.core.evidence_retest_completion import (
    RetestCompletionStore,
    approval_id_for_item,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_FAILED,
    RETEST_PASSED,
    RetestExecutionResult,
)
from artmach_assistant.core.evidence_retest_session import (
    RetestApprovalSession,
)


def _item(
    *,
    symbol: str = "Example.run",
    test_path: str = "tests/test_example.py",
) -> RetestItem:
    paths = (test_path,)

    return RetestItem(
        title=f"{symbol} yeniden testi",
        path="core/example.py",
        symbol=symbol,
        status=AUTOMATED,
        primary_test_paths=paths,
        test_paths=paths,
        command=(
            "python",
            "-m",
            "pytest",
            *paths,
            "-q",
        ),
    )


def _result(status: str) -> RetestExecutionResult:
    return RetestExecutionResult(
        status=status,
        title="Example.run",
        path="core/example.py",
        symbol="Example.run",
        returncode=(
            0 if status == RETEST_PASSED else 1
        ),
        reason="test sonucu",
    )


def test_completion_round_trip(tmp_path) -> None:
    store = RetestCompletionStore(
        tmp_path / "completed_retests.json"
    )
    session = RetestApprovalSession.create(
        _item()
    )

    stored = store.record(
        session,
        _result(RETEST_PASSED),
    )

    assert store.load() == (stored,)
    assert store.completed_ids() == frozenset(
        {session.approval_id}
    )


def test_record_replaces_same_approval_id(
    tmp_path,
) -> None:
    store = RetestCompletionStore(
        tmp_path / "completed_retests.json"
    )
    session = RetestApprovalSession.create(
        _item()
    )

    store.record(
        session,
        _result(RETEST_FAILED),
    )
    store.record(
        session,
        _result(RETEST_PASSED),
    )

    rows = store.load()

    assert len(rows) == 1
    assert rows[0].status == RETEST_PASSED


def test_different_items_have_different_ids() -> None:
    first = approval_id_for_item(
        _item(symbol="Example.run")
    )
    second = approval_id_for_item(
        _item(
            symbol="Example.stop",
            test_path="tests/test_stop.py",
        )
    )

    assert first != second


def test_clear_removes_history(tmp_path) -> None:
    store = RetestCompletionStore(
        tmp_path / "completed_retests.json"
    )
    session = RetestApprovalSession.create(
        _item()
    )

    store.record(
        session,
        _result(RETEST_PASSED),
    )
    store.clear()

    assert store.load() == ()
