from __future__ import annotations

import json

import pytest

from artmach_assistant.core.evidence_research import (
    EXTERNAL_APPROVAL_REQUIRED,
    EvidenceResearchPlan,
)
from artmach_assistant.core.evidence_research_session import (
    APPROVED,
    PENDING,
    EvidenceResearchApprovalSession,
    EvidenceResearchApprovalStore,
    approval_matches,
    cancellation_matches,
    render_pending_session,
)


def _plan() -> EvidenceResearchPlan:
    return EvidenceResearchPlan(
        status=EXTERNAL_APPROVAL_REQUIRED,
        title="Tekrarlanan hata: Example.run",
        path="core/example.py",
        symbol="Example.run",
        reason="Primary yeniden test basarisiz oldu.",
        local_questions=(
            "Yerel kodu incele.",
        ),
        external_queries=(
            "Example.run official documentation",
            "Example.run GitHub issues",
        ),
        preferred_sources=(
            "Resmi dokumantasyon",
            "Resmi GitHub deposu",
        ),
        safety_constraints=(
            "Internet kodu dogrudan uygulanamaz.",
        ),
    )


def test_session_is_deterministic() -> None:
    first = EvidenceResearchApprovalSession.create(
        _plan()
    )
    second = EvidenceResearchApprovalSession.create(
        _plan()
    )

    assert first.approval_id == second.approval_id
    assert first.status == PENDING
    assert first.approval_id.startswith("RS-")


def test_non_external_plan_is_rejected() -> None:
    plan = EvidenceResearchPlan(
        status="LOCAL_REVIEW",
        title="Example.run",
        path="core/example.py",
        symbol="Example.run",
        reason="Local review.",
    )

    with pytest.raises(
        ValueError,
        match="external research",
    ):
        EvidenceResearchApprovalSession.create(plan)


def test_store_round_trip(tmp_path) -> None:
    store = EvidenceResearchApprovalStore(
        tmp_path / "pending_research.json"
    )
    session = EvidenceResearchApprovalSession.create(
        _plan()
    )

    store.save(session)

    assert store.load() == session


def test_corrupt_session_is_rejected(tmp_path) -> None:
    path = tmp_path / "pending_research.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 999,
            }
        ),
        encoding="utf-8",
    )

    store = EvidenceResearchApprovalStore(path)

    with pytest.raises(
        ValueError,
        match="Unsupported",
    ):
        store.load()


def test_exact_approval_id_is_required() -> None:
    session = EvidenceResearchApprovalSession.create(
        _plan()
    )

    assert approval_matches(
        f"{session.approval_id} onayla",
        session,
    )
    assert not approval_matches(
        "internet arastirmasini onayla",
        session,
    )
    assert not approval_matches(
        "RS-0000000000 onayla",
        session,
    )


def test_status_can_advance() -> None:
    session = EvidenceResearchApprovalSession.create(
        _plan()
    )
    approved = session.with_status(APPROVED)

    assert approved.status == APPROVED
    assert approved.approval_id == session.approval_id


def test_cancellation_is_explicit() -> None:
    assert cancellation_matches(
        "internet arastirmasini iptal et"
    )
    assert not cancellation_matches("iptal")


def test_render_does_not_claim_execution() -> None:
    session = EvidenceResearchApprovalSession.create(
        _plan()
    )

    rendered = render_pending_session(session)

    assert session.approval_id in rendered
    assert "henuz baslatilmadi" in rendered
    assert "Example.run official documentation" in rendered
    assert "dogrudan uygulanamaz" in rendered
