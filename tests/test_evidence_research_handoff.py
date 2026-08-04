from __future__ import annotations

from artmach_assistant.core.evidence_research_handoff import (
    NO_RESEARCH,
    RESEARCH_APPROVAL_PENDING,
    RESEARCH_BLOCKED,
    EvidenceResearchHandoff,
)
from artmach_assistant.core.evidence_research_session import (
    EvidenceResearchApprovalStore,
)
from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_BLOCKED,
    RETEST_FAILED,
    RETEST_PASSED,
    RetestExecutionResult,
)


def _item(
    *,
    title: str = "Tekrarlanan hata: Example.run",
) -> RetestItem:
    paths = ("tests/test_example.py",)

    return RetestItem(
        title=title,
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


def _result(status: str) -> RetestExecutionResult:
    return RetestExecutionResult(
        status=status,
        title="Example.run",
        path="core/example.py",
        symbol="Example.run",
        returncode=(
            0 if status == RETEST_PASSED else 1
        ),
        reason="primary test sonucu",
    )


def _handoff(tmp_path) -> EvidenceResearchHandoff:
    return EvidenceResearchHandoff(
        store=EvidenceResearchApprovalStore(
            tmp_path / "pending_research.json"
        )
    )


def test_passed_retest_does_not_create_research_session(
    tmp_path,
) -> None:
    handoff = _handoff(tmp_path)

    outcome = handoff.handle_retest_result(
        _item(),
        _result(RETEST_PASSED),
    )

    assert outcome.status == NO_RESEARCH
    assert outcome.approval_session is None
    assert handoff.store.load() is None
    assert "Internet arastirmasi baslatilmadi" in (
        outcome.report
    )


def test_blocked_retest_does_not_create_research_session(
    tmp_path,
) -> None:
    handoff = _handoff(tmp_path)

    outcome = handoff.handle_retest_result(
        _item(),
        _result(RETEST_BLOCKED),
    )

    assert outcome.status == RESEARCH_BLOCKED
    assert outcome.approval_session is None
    assert handoff.store.load() is None


def test_failed_retest_creates_pending_rs_session(
    tmp_path,
) -> None:
    handoff = _handoff(tmp_path)

    outcome = handoff.handle_retest_result(
        _item(),
        _result(RETEST_FAILED),
    )

    assert outcome.status == RESEARCH_APPROVAL_PENDING
    assert outcome.approval_session is not None
    assert outcome.approval_session.approval_id.startswith(
        "RS-"
    )
    assert "DIS ARASTIRMA ONAYI" in outcome.report
    assert "henuz baslatilmadi" in outcome.report

    stored = handoff.store.load()

    assert stored == outcome.approval_session


def test_same_failed_retest_reuses_pending_session(
    tmp_path,
) -> None:
    handoff = _handoff(tmp_path)

    first = handoff.handle_retest_result(
        _item(),
        _result(RETEST_FAILED),
    )
    second = handoff.handle_retest_result(
        _item(),
        _result(RETEST_FAILED),
    )

    assert first.approval_session is not None
    assert second.approval_session is not None
    assert (
        first.approval_session.approval_id
        == second.approval_session.approval_id
    )


def test_different_failed_retest_does_not_replace_pending(
    tmp_path,
) -> None:
    handoff = _handoff(tmp_path)

    first = handoff.handle_retest_result(
        _item(),
        _result(RETEST_FAILED),
    )
    second = handoff.handle_retest_result(
        RetestItem(
            title="Tekrarlanan hata: Other.run",
            path="core/other.py",
            symbol="Other.run",
            status=AUTOMATED,
            primary_test_paths=(
                "tests/test_other.py",
            ),
            test_paths=(
                "tests/test_other.py",
            ),
            command=(
                "python",
                "-m",
                "pytest",
                "tests/test_other.py",
                "-q",
            ),
        ),
        RetestExecutionResult(
            status=RETEST_FAILED,
            title="Other.run",
            path="core/other.py",
            symbol="Other.run",
            returncode=1,
            reason="failed",
        ),
    )

    assert first.approval_session is not None
    assert second.approval_session is not None
    assert (
        second.approval_session.approval_id
        == first.approval_session.approval_id
    )
    assert "Yeni arastirma oturumu acilmadi" in (
        second.report
    )


def test_failed_warning_is_classified_as_researchable_debt(
    tmp_path,
) -> None:
    handoff = _handoff(tmp_path)

    outcome = handoff.handle_retest_result(
        _item(
            title=(
                "Tekrarlanan uyari: "
                "Example.slow_operation"
            )
        ),
        _result(RETEST_FAILED),
    )

    assert outcome.status == RESEARCH_APPROVAL_PENDING
    assert outcome.plan.status == (
        "EXTERNAL_APPROVAL_REQUIRED"
    )
