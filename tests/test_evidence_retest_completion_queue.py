from __future__ import annotations

from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
    RetestPlan,
)
from artmach_assistant.core.evidence_retest_command import (
    RetestCommandCoordinator,
)
from artmach_assistant.core.evidence_retest_completion import (
    RetestCompletionStore,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_PASSED,
    RetestExecutionResult,
)
from artmach_assistant.core.evidence_retest_session import (
    RetestApprovalStore,
)


def _item(
    symbol: str,
    test_path: str,
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


def _result(item: RetestItem) -> RetestExecutionResult:
    return RetestExecutionResult(
        status=RETEST_PASSED,
        title=item.title,
        path=item.path,
        symbol=item.symbol,
        returncode=0,
        reason="Primary yeniden testler gecti.",
    )


def _coordinator(tmp_path, plan):
    return RetestCommandCoordinator(
        store=RetestApprovalStore(
            tmp_path / "pending_retest.json"
        ),
        completion_store=RetestCompletionStore(
            tmp_path / "completed_retests.json"
        ),
        source_root=tmp_path,
        plan_provider=lambda: plan,
        executor=lambda item, **_kwargs: _result(item),
    )


def test_completed_first_item_is_skipped(
    tmp_path,
) -> None:
    source = tmp_path / "core" / "example.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    first = _item(
        "Example.first",
        "tests/test_first.py",
    )
    second = _item(
        "Example.second",
        "tests/test_second.py",
    )
    plan = RetestPlan((first, second))
    coordinator = _coordinator(tmp_path, plan)

    coordinator.handle("retest planini baslat")
    session = coordinator.store.load()
    assert session is not None
    assert session.symbol == "Example.first"

    coordinator.handle(
        f"{session.approval_id} onayla"
    )

    rendered = coordinator.handle(
        "retest planini baslat"
    )

    assert rendered is not None
    assert "Example.second" in rendered

    next_session = coordinator.store.load()
    assert next_session is not None
    assert next_session.symbol == "Example.second"


def test_all_completed_items_return_empty_queue(
    tmp_path,
) -> None:
    source = tmp_path / "core" / "example.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    item = _item(
        "Example.only",
        "tests/test_only.py",
    )
    plan = RetestPlan((item,))
    coordinator = _coordinator(tmp_path, plan)

    coordinator.handle("retest planini baslat")
    session = coordinator.store.load()
    assert session is not None

    coordinator.handle(
        f"{session.approval_id} onayla"
    )

    rendered = coordinator.handle(
        "retest planini baslat"
    )

    assert rendered is not None
    assert "uygun" in rendered
    assert "Hicbir test calistirilmadi" in rendered


def test_completion_history_failure_does_not_block_result(
    tmp_path,
) -> None:
    item = _item(
        "Example.run",
        "tests/test_example.py",
    )

    class BrokenCompletionStore:
        def completed_ids(self):
            return frozenset()

        def record(self, _session, _result):
            raise OSError("disk error")

    coordinator = RetestCommandCoordinator(
        store=RetestApprovalStore(
            tmp_path / "pending_retest.json"
        ),
        completion_store=BrokenCompletionStore(),
        source_root=tmp_path,
        plan_provider=lambda: RetestPlan((item,)),
        executor=lambda current, **_kwargs: _result(
            current
        ),
    )

    coordinator.handle("retest planini baslat")
    session = coordinator.store.load()
    assert session is not None

    rendered = coordinator.handle(
        f"{session.approval_id} onayla"
    )

    assert rendered is not None
    assert "Durum: PASSED" in rendered
    assert "completion history" in rendered

def test_source_changed_after_completion_is_requeued(
    tmp_path,
) -> None:
    import os
    from datetime import datetime, timezone

    first = _item(
        "Example.first",
        "tests/test_first.py",
    )
    plan = RetestPlan((first,))
    coordinator = _coordinator(tmp_path, plan)

    source = tmp_path / "core" / "example.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    coordinator.handle("retest planini baslat")
    session = coordinator.store.load()

    assert session is not None

    coordinator.handle(
        f"{session.approval_id} onayla"
    )

    records = coordinator.completion_store.load()

    assert len(records) == 1

    completion_time = datetime.fromisoformat(
        records[0].completed_at.replace(
            "Z",
            "+00:00",
        )
    )

    changed_at = completion_time.timestamp() + 5.0
    os.utime(
        source,
        (changed_at, changed_at),
    )

    rendered = coordinator.handle(
        "retest planini baslat"
    )

    assert rendered is not None
    assert "Example.first" in rendered

    next_session = coordinator.store.load()

    assert next_session is not None
    assert next_session.symbol == "Example.first"


def test_unchanged_source_remains_completed(
    tmp_path,
) -> None:
    import os
    from datetime import datetime

    first = _item(
        "Example.first",
        "tests/test_first.py",
    )
    plan = RetestPlan((first,))
    coordinator = _coordinator(tmp_path, plan)

    source = tmp_path / "core" / "example.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    coordinator.handle("retest planini baslat")
    session = coordinator.store.load()

    assert session is not None

    coordinator.handle(
        f"{session.approval_id} onayla"
    )

    records = coordinator.completion_store.load()

    assert len(records) == 1

    completion_time = datetime.fromisoformat(
        records[0].completed_at.replace(
            "Z",
            "+00:00",
        )
    )

    old_time = completion_time.timestamp() - 5.0
    os.utime(
        source,
        (old_time, old_time),
    )

    rendered = coordinator.handle(
        "retest planini baslat"
    )

    assert rendered is not None
    assert "uygun" in rendered
