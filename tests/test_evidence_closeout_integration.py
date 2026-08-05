from __future__ import annotations

import os
from datetime import datetime, timezone

import artmach_assistant.core.assistant as assistant_module
from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.evidence_closeout import (
    RESOLVED_CANDIDATE,
)
from artmach_assistant.core.evidence_maintenance import (
    EvidenceMaintenanceFinding,
    EvidenceMaintenanceReport,
)
from artmach_assistant.core.evidence_retest_completion import (
    RetestCompletionRecord,
)
from artmach_assistant.core.evidence_retest_executor import (
    RETEST_PASSED,
)


def _finding() -> EvidenceMaintenanceFinding:
    return EvidenceMaintenanceFinding(
        classification="A",
        score=90,
        source="runtime",
        title="Tekrarlanan hata: Example.run",
        path="core/example.py",
        symbol="Example.run",
        evidence="4 tekrar",
        repair_candidate=False,
        lifecycle="NEEDS_RETEST",
    )


def _record() -> RetestCompletionRecord:
    return RetestCompletionRecord(
        approval_id="RT-ABCDEF1234",
        status=RETEST_PASSED,
        title="Example.run yeniden testi",
        path="core/example.py",
        symbol="Example.run",
        primary_test_paths=(
            "tests/test_example.py",
        ),
        returncode=0,
        completed_at="2026-08-05T09:00:00+00:00",
    )


def test_report_counts_resolved_candidates() -> None:
    report = EvidenceMaintenanceReport(
        (
            _finding(),
            EvidenceMaintenanceFinding(
                classification="A",
                score=90,
                source="runtime",
                title="Resolved",
                path="core/resolved.py",
                symbol="Resolved.run",
                lifecycle=RESOLVED_CANDIDATE,
            ),
        )
    )

    rendered = report.report()

    assert "yeniden test edilmeli: 1" in rendered
    assert "cozulmus aday: 1" in rendered


def test_assistant_applies_completion_closeout(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "core" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )

    changed_at = datetime(
        2026,
        8,
        5,
        8,
        0,
        tzinfo=timezone.utc,
    ).timestamp()
    os.utime(source, (changed_at, changed_at))

    class FakeStore:
        def __init__(self, _path) -> None:
            pass

        def load(self):
            return (_record(),)

    monkeypatch.setattr(
        assistant_module,
        "RetestCompletionStore",
        FakeStore,
    )

    engine = AssistantEngine.__new__(AssistantEngine)

    closed = engine._apply_completed_retest_closeout(
        EvidenceMaintenanceReport((_finding(),)),
        source_root=tmp_path,
    )

    assert len(closed.findings) == 1
    assert (
        closed.findings[0].lifecycle
        == RESOLVED_CANDIDATE
    )
    assert closed.findings[0].repair_candidate is False


def test_resolved_candidate_is_not_in_retest_plan(
    tmp_path,
) -> None:
    from artmach_assistant.core.evidence_retest import (
        build_retest_plan,
    )

    finding = EvidenceMaintenanceFinding(
        classification="A",
        score=90,
        source="runtime",
        title="Resolved",
        path="core/example.py",
        symbol="Example.run",
        lifecycle=RESOLVED_CANDIDATE,
    )

    plan = build_retest_plan(
        (finding,),
        source_root=tmp_path,
    )

    assert plan.items == ()
