from __future__ import annotations

from artmach_assistant.core.evidence_patch_outcome import (
    OUTCOME_PARTIAL,
    OUTCOME_RECORDED,
    record_patch_outcome,
)
from artmach_assistant.core.evidence_patch_session import EvidencePatchSession


class _History:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.rows = []

    def record(self, event: str, **details) -> None:
        if self.fail:
            raise OSError("history unavailable")
        self.rows.append((event, details))


class _Learning:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.rows = []

    def audit(self, event: str, **details: str) -> None:
        if self.fail:
            raise OSError("audit unavailable")
        self.rows.append((event, details))


def _session() -> EvidencePatchSession:
    return EvidencePatchSession.create(
        proposal_id="PP-OUTCOME",
        target_path="core/task_orchestrator.py",
        target_symbol="TaskOrchestrator.wrap.execute",
    )


def test_successful_outcome_records_history_and_learning() -> None:
    history = _History()
    learning = _Learning()
    result = record_patch_outcome(
        _session(),
        history=history,
        learning=learning,
        successful=True,
        note="primary retest passed",
    )
    assert result.status == OUTCOME_RECORDED
    assert history.rows[0][1]["outcome"] == "successful"
    assert learning.rows[0][1]["outcome"] == "successful"


def test_partial_outcome_does_not_hide_audit_failure() -> None:
    result = record_patch_outcome(
        _session(),
        history=_History(),
        learning=_Learning(fail=True),
        successful=False,
        note="rollback completed",
    )
    assert result.status == OUTCOME_PARTIAL
    assert "OSError" in result.error
