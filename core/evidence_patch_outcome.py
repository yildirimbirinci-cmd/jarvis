from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from artmach_assistant.core.evidence_patch_session import EvidencePatchSession


class _HistoryRecorder(Protocol):
    def record(self, event: str, **details: str | int | bool) -> None: ...


class _LearningAuditor(Protocol):
    def audit(self, event: str, **details: str) -> None: ...


OUTCOME_RECORDED = "RECORDED"
OUTCOME_PARTIAL = "PARTIAL"


@dataclass(frozen=True, slots=True)
class EvidencePatchOutcomeResult:
    status: str
    journal_summary: str
    memory_summary: str
    error: str = ""

    @property
    def recorded(self) -> bool:
        return self.status == OUTCOME_RECORDED


def record_patch_outcome(
    session: EvidencePatchSession,
    *,
    history: _HistoryRecorder,
    learning: _LearningAuditor,
    successful: bool,
    note: str,
) -> EvidencePatchOutcomeResult:
    """Persist one bounded patch outcome without changing source code."""
    outcome = "successful" if successful else "failed"
    clean_note = str(note or "").strip()[:2000]
    history_error = ""
    memory_error = ""

    try:
        history.record(
            "evidence_patch_outcome",
            session_id=session.session_id,
            proposal_id=session.proposal_id,
            outcome=outcome,
            target_path=session.target_path,
            target_symbol=session.target_symbol,
            note=clean_note,
        )
    except Exception as exc:
        history_error = f"{type(exc).__name__}: {exc}"

    try:
        learning.audit(
            "evidence_patch_outcome",
            session_id=session.session_id,
            outcome=outcome,
            target=session.target_path,
            symbol=session.target_symbol,
            note=clean_note,
        )
    except Exception as exc:
        memory_error = f"{type(exc).__name__}: {exc}"

    journal_summary = (
        "Patch sonucu kendi kaynak islem gecmisine yazildi."
        if not history_error
        else "Patch sonucu kendi kaynak islem gecmisine yazilamadi."
    )
    memory_summary = (
        "Patch sonucu yerel ogrenme audit kaydina yazildi."
        if not memory_error
        else "Patch sonucu yerel ogrenme audit kaydina yazilamadi."
    )
    errors = "; ".join(item for item in (history_error, memory_error) if item)
    return EvidencePatchOutcomeResult(
        status=(OUTCOME_RECORDED if not errors else OUTCOME_PARTIAL),
        journal_summary=journal_summary,
        memory_summary=memory_summary,
        error=errors,
    )
