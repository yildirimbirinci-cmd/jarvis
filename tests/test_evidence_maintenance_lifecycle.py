from __future__ import annotations

import os
from datetime import datetime, timezone

from artmach_assistant.core.evidence_lifecycle import (
    ACTIVE,
    NEEDS_RETEST,
    SourceLifecycleResolver,
)
from artmach_assistant.core.evidence_maintenance import (
    build_evidence_maintenance_report,
)
from artmach_assistant.core.runtime_observability import RuntimeFinding


def _finding(last_seen: str) -> RuntimeFinding:
    return RuntimeFinding(
        finding_id="RUN-ABCDEF1234",
        severity="high",
        category="repeated_runtime_failure",
        title="Tekrarlanan hata: Example.run",
        explanation="Runtime hatasi.",
        confidence=0.95,
        occurrence_count=4,
        last_seen=last_seen,
        workspace="C:/repo",
        scope="own_code",
        affected_paths=("core/example.py",),
        affected_symbols=("Example.run",),
        evidence=(),
        recommendation="Incele.",
        acceptance_criteria=(
            "Hata tekrar etmemeli.",
        ),
        research_query="",
    )


def _write_source(
    tmp_path,
    *,
    timestamp: float,
) -> None:
    target = tmp_path / "core" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    os.utime(target, (timestamp, timestamp))


def test_source_changed_after_failure_requires_retest(
    tmp_path,
) -> None:
    changed_at = datetime(
        2026,
        8,
        4,
        12,
        0,
        tzinfo=timezone.utc,
    )
    _write_source(
        tmp_path,
        timestamp=changed_at.timestamp(),
    )

    report = build_evidence_maintenance_report(
        (),
        (
            _finding(
                "2026-08-04T10:00:00+00:00"
            ),
        ),
        source_root=tmp_path,
    )

    finding = report.findings[0]

    assert finding.lifecycle == NEEDS_RETEST
    assert finding.repair_candidate is False


def test_failure_after_latest_source_change_remains_active(
    tmp_path,
) -> None:
    changed_at = datetime(
        2026,
        8,
        4,
        9,
        0,
        tzinfo=timezone.utc,
    )
    _write_source(
        tmp_path,
        timestamp=changed_at.timestamp(),
    )

    report = build_evidence_maintenance_report(
        (),
        (
            _finding(
                "2026-08-04T10:00:00+00:00"
            ),
        ),
        source_root=tmp_path,
    )

    finding = report.findings[0]

    assert finding.lifecycle == ACTIVE
    assert finding.repair_candidate is True


def test_missing_source_stays_active_conservatively(
    tmp_path,
) -> None:
    resolver = SourceLifecycleResolver(tmp_path)

    assert resolver.classify(
        _finding("2026-08-04T10:00:00+00:00")
    ) == ACTIVE


def test_report_displays_lifecycle_summary(
    tmp_path,
) -> None:
    changed_at = datetime(
        2026,
        8,
        4,
        12,
        0,
        tzinfo=timezone.utc,
    )
    _write_source(
        tmp_path,
        timestamp=changed_at.timestamp(),
    )

    rendered = build_evidence_maintenance_report(
        (),
        (
            _finding(
                "2026-08-04T10:00:00+00:00"
            ),
        ),
        source_root=tmp_path,
    ).report()

    assert "yeniden test edilmeli: 1" in rendered
    assert "Durum: NEEDS_RETEST" in rendered
    assert "Otomatik onarim adayi: hayir" in rendered
