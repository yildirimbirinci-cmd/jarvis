from __future__ import annotations

import os
from datetime import datetime, timezone

from artmach_assistant.core.evidence_closeout import (
    RESOLVED_CANDIDATE,
    apply_retest_closeout,
)
from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
)
from artmach_assistant.core.evidence_retest_completion import (
    RetestCompletionRecord,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_FAILED,
    RETEST_PASSED,
)


def _finding(
    *,
    path: str = "core/example.py",
    symbol: str = "Example.run",
) -> EvidenceMaintenanceFinding:
    return EvidenceMaintenanceFinding(
        classification="A",
        score=90,
        source="runtime",
        title="Tekrarlanan hata: Example.run",
        path=path,
        symbol=symbol,
        evidence="4 tekrar",
        repair_candidate=False,
        lifecycle="NEEDS_RETEST",
    )


def _record(
    *,
    status: str = RETEST_PASSED,
    completed_at: str = (
        "2026-08-05T08:00:00+00:00"
    ),
    path: str = "core/example.py",
    symbol: str = "Example.run",
) -> RetestCompletionRecord:
    return RetestCompletionRecord(
        approval_id="RT-ABCDEF1234",
        status=status,
        title="Example.run yeniden testi",
        path=path,
        symbol=symbol,
        primary_test_paths=(
            "tests/test_example.py",
        ),
        returncode=(
            0 if status == RETEST_PASSED else 1
        ),
        completed_at=completed_at,
    )


def _write_source(
    tmp_path,
    *,
    changed_at: datetime,
) -> None:
    target = tmp_path / "core" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    timestamp = changed_at.timestamp()
    os.utime(target, (timestamp, timestamp))


def test_passed_retest_closes_matching_finding(
    tmp_path,
) -> None:
    _write_source(
        tmp_path,
        changed_at=datetime(
            2026,
            8,
            5,
            7,
            0,
            tzinfo=timezone.utc,
        ),
    )

    findings = apply_retest_closeout(
        (_finding(),),
        (_record(),),
        source_root=tmp_path,
    )

    assert len(findings) == 1
    assert (
        findings[0].lifecycle
        == RESOLVED_CANDIDATE
    )
    assert findings[0].repair_candidate is False
    assert "primary retest PASSED" in (
        findings[0].evidence
    )


def test_failed_retest_does_not_close_finding(
    tmp_path,
) -> None:
    findings = apply_retest_closeout(
        (_finding(),),
        (
            _record(
                status=RETEST_FAILED,
            ),
        ),
        source_root=tmp_path,
    )

    assert findings[0].lifecycle == (
        "NEEDS_RETEST"
    )


def test_different_symbol_does_not_close_finding(
    tmp_path,
) -> None:
    findings = apply_retest_closeout(
        (_finding(symbol="Example.run"),),
        (
            _record(
                symbol="Example.stop",
            ),
        ),
        source_root=tmp_path,
    )

    assert findings[0].lifecycle == (
        "NEEDS_RETEST"
    )


def test_source_changed_after_completion_reopens_finding(
    tmp_path,
) -> None:
    _write_source(
        tmp_path,
        changed_at=datetime(
            2026,
            8,
            5,
            9,
            0,
            tzinfo=timezone.utc,
        ),
    )

    findings = apply_retest_closeout(
        (_finding(),),
        (
            _record(
                completed_at=(
                    "2026-08-05T08:00:00+00:00"
                ),
            ),
        ),
        source_root=tmp_path,
    )

    assert findings[0].lifecycle == (
        "NEEDS_RETEST"
    )


def test_static_finding_is_not_closed(
    tmp_path,
) -> None:
    static = EvidenceMaintenanceFinding(
        classification="C",
        score=10,
        source="static",
        title="[STYLE] uzun satir",
        path="core/example.py",
        symbol="Example.run",
        lifecycle="STATIC",
    )

    findings = apply_retest_closeout(
        (static,),
        (_record(),),
        source_root=tmp_path,
    )

    assert findings[0].lifecycle == "STATIC"


def test_latest_passed_record_is_used(
    tmp_path,
) -> None:
    _write_source(
        tmp_path,
        changed_at=datetime(
            2026,
            8,
            5,
            8,
            30,
            tzinfo=timezone.utc,
        ),
    )

    findings = apply_retest_closeout(
        (_finding(),),
        (
            _record(
                completed_at=(
                    "2026-08-05T08:00:00+00:00"
                ),
            ),
            RetestCompletionRecord(
                approval_id="RT-FFFFFFFFFF",
                status=RETEST_PASSED,
                title="Example.run yeniden testi",
                path="core/example.py",
                symbol="Example.run",
                primary_test_paths=(
                    "tests/test_example.py",
                ),
                returncode=0,
                completed_at=(
                    "2026-08-05T09:00:00+00:00"
                ),
            ),
        ),
        source_root=tmp_path,
    )

    assert (
        findings[0].lifecycle
        == RESOLVED_CANDIDATE
    )
    assert "2026-08-05T09:00:00+00:00" in (
        findings[0].evidence
    )
