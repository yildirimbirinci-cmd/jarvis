from __future__ import annotations

import json

import pytest

from artmach_assistant.core.evidence_retest import (
    AUTOMATED,
    RetestItem,
)
from artmach_assistant.core.evidence_retest_session import (
    APPROVED,
    PENDING,
    RetestApprovalSession,
    RetestApprovalStore,
    approval_matches,
    cancellation_matches,
    render_pending_session,
)


def _item() -> RetestItem:
    paths = (
        "tests/test_one.py",
        "tests/test_two.py",
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


def test_session_is_deterministic_for_same_item() -> None:
    first = RetestApprovalSession.create(_item())
    second = RetestApprovalSession.create(_item())

    assert first.approval_id == second.approval_id
    assert first.status == PENDING


def test_store_round_trip(tmp_path) -> None:
    store = RetestApprovalStore(
        tmp_path / "pending_retest.json"
    )
    session = RetestApprovalSession.create(_item())

    store.save(session)
    loaded = store.load()

    assert loaded == session


def test_corrupt_session_is_rejected(tmp_path) -> None:
    path = tmp_path / "pending_retest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 999,
            }
        ),
        encoding="utf-8",
    )

    store = RetestApprovalStore(path)

    with pytest.raises(
        ValueError,
        match="Unsupported",
    ):
        store.load()


def test_exact_approval_id_is_required() -> None:
    session = RetestApprovalSession.create(_item())

    assert approval_matches(
        f"{session.approval_id} onayla",
        session,
    )
    assert not approval_matches(
        "onayla",
        session,
    )
    assert not approval_matches(
        "baska-test onayla",
        session,
    )


def test_session_status_can_advance() -> None:
    session = RetestApprovalSession.create(_item())
    approved = session.with_status(APPROVED)

    assert session.status == PENDING
    assert approved.status == APPROVED
    assert approved.approval_id == session.approval_id


def test_cancellation_intent_is_explicit() -> None:
    assert cancellation_matches(
        "yeniden testi iptal et"
    )
    assert not cancellation_matches("iptal")


def test_render_does_not_claim_execution() -> None:
    session = RetestApprovalSession.create(_item())

    rendered = render_pending_session(session)

    assert session.approval_id in rendered
    assert "Henuz test calistirilmadi" in rendered
    assert "tests/test_one.py" in rendered
    assert "uygulandi" not in rendered.casefold()
