from __future__ import annotations

import json

from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
    RetestPlan,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_PASSED,
    RetestExecutionResult,
)
from artmach_assistant.core.evidence_retest_session import (
    COMPLETED,
    PENDING,
    RetestApprovalStore,
)
from artmach_assistant.core.evidence_retest_command import (
    RetestCommandCoordinator,
    is_retest_start_request,
)


def _item() -> RetestItem:
    paths = (
        "tests/test_example.py",
    )
    return RetestItem(
        title="Example.run yeniden testi",
        path="core/example.py",
        symbol="Example.run",
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


def _coordinator(
    tmp_path,
    *,
    plan: RetestPlan | None = None,
    executor=None,
) -> RetestCommandCoordinator:
    kwargs = {}

    if executor is not None:
        kwargs["executor"] = executor

    return RetestCommandCoordinator(
        store=RetestApprovalStore(
            tmp_path / "pending_retest.json"
        ),
        source_root=tmp_path,
        plan_provider=lambda: (
            plan
            if plan is not None
            else RetestPlan((_item(),))
        ),
        **kwargs,
    )


def test_turkish_start_request_is_recognized() -> None:
    assert is_retest_start_request(
        "Yeniden test edilmesi gereken "
        "bulgular\u0131 do\u011frula."
    )


def test_start_request_creates_pending_session(
    tmp_path,
) -> None:
    coordinator = _coordinator(tmp_path)

    rendered = coordinator.handle(
        "yeniden test edilmesi gereken "
        "bulgulari dogrula"
    )

    assert rendered is not None
    assert "PRIMARY YENIDEN TEST ONAYI" in rendered
    assert "Henuz test calistirilmadi" in rendered

    session = coordinator.store.load()

    assert session is not None
    assert session.status == PENDING


def test_existing_pending_session_is_reused(
    tmp_path,
) -> None:
    coordinator = _coordinator(tmp_path)

    first = coordinator.handle(
        "retest planini baslat"
    )
    second = coordinator.handle(
        "retest planini baslat"
    )

    assert first == second


def test_exact_approval_executes_primary_tests(
    tmp_path,
) -> None:
    calls = []

    def executor(item, **kwargs):
        calls.append((item, kwargs))
        return RetestExecutionResult(
            status=RETEST_PASSED,
            title=item.title,
            path=item.path,
            symbol=item.symbol,
            command=item.command,
            returncode=0,
            duration_ms=12.0,
            stdout_tail="1 passed",
            reason="Primary yeniden testler gecti.",
        )

    coordinator = _coordinator(
        tmp_path,
        executor=executor,
    )
    coordinator.handle("retest planini baslat")
    session = coordinator.store.load()

    assert session is not None

    rendered = coordinator.handle(
        f"{session.approval_id} onayla"
    )

    assert rendered is not None
    assert "Durum: PASSED" in rendered
    assert "Kaynak kodu degistirilmedi" in rendered
    assert len(calls) == 1

    completed = coordinator.store.load()

    assert completed is not None
    assert completed.status == COMPLETED


def test_wrong_approval_id_does_not_execute(
    tmp_path,
) -> None:
    calls = []

    def executor(item, **kwargs):
        calls.append((item, kwargs))
        raise AssertionError(
            "executor must not run"
        )

    coordinator = _coordinator(
        tmp_path,
        executor=executor,
    )
    coordinator.handle("retest planini baslat")

    rendered = coordinator.handle(
        "RT-0000000000 onayla"
    )

    assert rendered is not None
    assert "gecersiz" in rendered
    assert calls == []


def test_explicit_cancellation_stops_pending_session(
    tmp_path,
) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.handle("retest planini baslat")

    rendered = coordinator.handle(
        "yeniden testi iptal et"
    )

    assert rendered is not None
    assert "iptal edildi" in rendered
    assert "Hicbir test calistirilmadi" in rendered


def test_unrelated_text_is_not_consumed(
    tmp_path,
) -> None:
    coordinator = _coordinator(tmp_path)

    assert coordinator.handle(
        "bugun hava nasil"
    ) is None


def test_empty_plan_does_not_create_session(
    tmp_path,
) -> None:
    coordinator = _coordinator(
        tmp_path,
        plan=RetestPlan(()),
    )

    rendered = coordinator.handle(
        "retest planini baslat"
    )

    assert rendered is not None
    assert "uygun" in rendered
    assert coordinator.store.load() is None


def test_corrupt_store_is_cleared_safely(
    tmp_path,
) -> None:
    path = tmp_path / "pending_retest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 999,
            }
        ),
        encoding="utf-8",
    )

    coordinator = RetestCommandCoordinator(
        store=RetestApprovalStore(path),
        source_root=tmp_path,
        plan_provider=lambda: RetestPlan(()),
    )

    rendered = coordinator.handle(
        "retest planini baslat"
    )

    assert rendered is not None
    assert "bozuk" in rendered
    assert not path.exists()
