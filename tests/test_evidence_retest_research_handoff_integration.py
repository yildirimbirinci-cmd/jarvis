from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
    RetestPlan,
)
from artmach_assistant.core.evidence_retest_command import (
    RetestCommandCoordinator,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_FAILED,
    RETEST_PASSED,
    RetestExecutionResult,
)
from artmach_assistant.core.evidence_retest_session import (
    RetestApprovalStore,
)


def _item() -> RetestItem:
    paths = ("tests/test_example.py",)
    return RetestItem(
        title="Tekrarlanan hata: Example.run",
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


def _result(
    item: RetestItem,
    status: str,
) -> RetestExecutionResult:
    return RetestExecutionResult(
        status=status,
        title=item.title,
        path=item.path,
        symbol=item.symbol,
        returncode=(
            0 if status == RETEST_PASSED else 1
        ),
        reason="primary test result",
    )


def test_failed_retest_appends_research_approval(
    tmp_path,
) -> None:
    item = _item()

    coordinator = RetestCommandCoordinator(
        store=RetestApprovalStore(
            tmp_path / "pending_retest.json"
        ),
        source_root=tmp_path,
        plan_provider=lambda: RetestPlan((item,)),
        executor=lambda current, **_kwargs: _result(
            current,
            RETEST_FAILED,
        ),
        result_handler=lambda _item, _result: (
            "DIS ARASTIRMA ONAYI\n"
            "Onay kimligi: RS-ABCDEF1234\n"
            "Internet arastirmasi henuz baslatilmadi."
        ),
    )

    coordinator.handle("retest planini baslat")
    session = coordinator.store.load()

    assert session is not None

    rendered = coordinator.handle(
        f"{session.approval_id} onayla"
    )

    assert rendered is not None
    assert "Durum: FAILED" in rendered
    assert "DIS ARASTIRMA ONAYI" in rendered
    assert "RS-ABCDEF1234" in rendered


def test_passed_retest_does_not_append_research(
    tmp_path,
) -> None:
    item = _item()

    coordinator = RetestCommandCoordinator(
        store=RetestApprovalStore(
            tmp_path / "pending_retest.json"
        ),
        source_root=tmp_path,
        plan_provider=lambda: RetestPlan((item,)),
        executor=lambda current, **_kwargs: _result(
            current,
            RETEST_PASSED,
        ),
        result_handler=lambda _item, _result: None,
    )

    coordinator.handle("retest planini baslat")
    session = coordinator.store.load()

    assert session is not None

    rendered = coordinator.handle(
        f"{session.approval_id} onayla"
    )

    assert rendered is not None
    assert "Durum: PASSED" in rendered
    assert "DIS ARASTIRMA ONAYI" not in rendered


def test_assistant_returns_only_pending_research_report() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)

    class Outcome:
        approval_session = object()
        report = "RS_REPORT"

    class Handoff:
        def handle_retest_result(self, _item, _result):
            return Outcome()

    engine.evidence_research_handoff = Handoff()

    assert engine._handle_retest_research_handoff(
        object(),
        object(),
    ) == "RS_REPORT"


def test_assistant_ignores_non_research_outcome() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)

    class Outcome:
        approval_session = None
        report = "NO_RESEARCH"

    class Handoff:
        def handle_retest_result(self, _item, _result):
            return Outcome()

    engine.evidence_research_handoff = Handoff()

    assert engine._handle_retest_research_handoff(
        object(),
        object(),
    ) is None
