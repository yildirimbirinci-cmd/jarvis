from __future__ import annotations

import json

from artmach_assistant.core.evidence_research_command import (
    EvidenceResearchCommandCoordinator,
)
from artmach_assistant.core.evidence_research_executor import (
    EvidenceResearchExecutionResult,
    RESEARCH_COMPLETED,
    RESEARCH_FAILED,
    RESEARCH_PARTIAL,
)
from artmach_assistant.core.evidence_research_session import (
    CANCELLED,
    COMPLETED,
    FAILED,
    PENDING,
    EvidenceResearchApprovalSession,
    EvidenceResearchApprovalStore,
)


def _session(
    *,
    status: str = PENDING,
) -> EvidenceResearchApprovalSession:
    return EvidenceResearchApprovalSession(
        schema_version=1,
        approval_id="RS-ABCDEF1234",
        status=status,
        title="Example.run failure",
        path="core/example.py",
        symbol="Example.run",
        reason="Primary yeniden test basarisiz oldu.",
        local_questions=(
            "Yerel kodu incele.",
        ),
        external_queries=(
            "Example.run official documentation",
        ),
        preferred_sources=(
            "Resmi dokumantasyon",
        ),
        safety_constraints=(
            "Internet kodu dogrudan uygulanamaz.",
        ),
        created_at="2026-08-05T08:00:00+00:00",
        updated_at="2026-08-05T08:00:00+00:00",
    )


def _result(
    *,
    status: str = RESEARCH_COMPLETED,
) -> EvidenceResearchExecutionResult:
    return EvidenceResearchExecutionResult(
        status=status,
        approval_id="RS-ABCDEF1234",
        title="Example.run failure",
        path="core/example.py",
        symbol="Example.run",
        queries=(
            "Example.run official documentation",
        ),
        reason="Arastirma sonucu.",
    )


def _coordinator(
    tmp_path,
    *,
    executor=None,
) -> EvidenceResearchCommandCoordinator:
    store = EvidenceResearchApprovalStore(
        tmp_path / "pending_research.json"
    )
    store.save(_session())

    kwargs = {}

    if executor is not None:
        kwargs["executor"] = executor

    return EvidenceResearchCommandCoordinator(
        store=store,
        **kwargs,
    )


def test_exact_rs_approval_runs_executor(
    tmp_path,
) -> None:
    calls = []

    def executor(session):
        calls.append(session)
        return _result()

    coordinator = _coordinator(
        tmp_path,
        executor=executor,
    )

    rendered = coordinator.handle(
        "RS-ABCDEF1234 onayla"
    )

    assert rendered is not None
    assert "DIS ARASTIRMA SONUCU" in rendered
    assert "Durum: COMPLETED" in rendered
    assert len(calls) == 1
    assert calls[0].status == "APPROVED"

    stored = coordinator.store.load()

    assert stored is not None
    assert stored.status == COMPLETED


def test_partial_research_is_completed_session(
    tmp_path,
) -> None:
    coordinator = _coordinator(
        tmp_path,
        executor=lambda _session: _result(
            status=RESEARCH_PARTIAL
        ),
    )

    rendered = coordinator.handle(
        "RS-ABCDEF1234 onayla"
    )

    assert rendered is not None
    assert "Durum: PARTIAL" in rendered

    stored = coordinator.store.load()

    assert stored is not None
    assert stored.status == COMPLETED


def test_failed_result_marks_session_failed(
    tmp_path,
) -> None:
    coordinator = _coordinator(
        tmp_path,
        executor=lambda _session: _result(
            status=RESEARCH_FAILED
        ),
    )

    rendered = coordinator.handle(
        "RS-ABCDEF1234 onayla"
    )

    assert rendered is not None
    assert "Durum: FAILED" in rendered

    stored = coordinator.store.load()

    assert stored is not None
    assert stored.status == FAILED


def test_executor_exception_isolated(
    tmp_path,
) -> None:
    def executor(_session):
        raise RuntimeError("network unavailable")

    coordinator = _coordinator(
        tmp_path,
        executor=executor,
    )

    rendered = coordinator.handle(
        "RS-ABCDEF1234 onayla"
    )

    assert rendered is not None
    assert "Durum: FAILED" in rendered
    assert "RuntimeError" in rendered

    stored = coordinator.store.load()

    assert stored is not None
    assert stored.status == FAILED


def test_wrong_rs_id_does_not_execute(
    tmp_path,
) -> None:
    calls = []

    def executor(session):
        calls.append(session)
        return _result()

    coordinator = _coordinator(
        tmp_path,
        executor=executor,
    )

    rendered = coordinator.handle(
        "RS-0000000000 onayla"
    )

    assert rendered is not None
    assert "gecersiz" in rendered
    assert calls == []

    stored = coordinator.store.load()

    assert stored is not None
    assert stored.status == PENDING


def test_general_approval_is_not_consumed(
    tmp_path,
) -> None:
    coordinator = _coordinator(tmp_path)

    assert coordinator.handle("onayla") is None


def test_explicit_cancellation(
    tmp_path,
) -> None:
    coordinator = _coordinator(tmp_path)

    rendered = coordinator.handle(
        "internet arastirmasini iptal et"
    )

    assert rendered is not None
    assert "iptal edildi" in rendered
    assert "baslatilmadi" in rendered

    stored = coordinator.store.load()

    assert stored is not None
    assert stored.status == CANCELLED


def test_completed_session_cannot_run_again(
    tmp_path,
) -> None:
    store = EvidenceResearchApprovalStore(
        tmp_path / "pending_research.json"
    )
    store.save(
        _session(status=COMPLETED)
    )

    coordinator = EvidenceResearchCommandCoordinator(
        store=store,
    )

    rendered = coordinator.handle(
        "RS-ABCDEF1234 onayla"
    )

    assert rendered is not None
    assert "yeniden calistirilamaz" in rendered


def test_pending_report_returns_session_text(
    tmp_path,
) -> None:
    coordinator = _coordinator(tmp_path)

    rendered = coordinator.pending_report()

    assert rendered is not None
    assert "DIS ARASTIRMA ONAYI" in rendered
    assert "RS-ABCDEF1234" in rendered
    assert "henuz baslatilmadi" in rendered


def test_corrupt_store_is_cleared(
    tmp_path,
) -> None:
    path = tmp_path / "pending_research.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 999,
            }
        ),
        encoding="utf-8",
    )

    coordinator = EvidenceResearchCommandCoordinator(
        store=EvidenceResearchApprovalStore(path),
    )

    rendered = coordinator.handle(
        "RS-ABCDEF1234 onayla"
    )

    assert rendered is not None
    assert "bozuk" in rendered
    assert "baslatilmadi" in rendered
    assert not path.exists()


def test_unrelated_text_is_not_consumed(
    tmp_path,
) -> None:
    coordinator = _coordinator(tmp_path)

    assert coordinator.handle(
        "bugun hava nasil"
    ) is None
