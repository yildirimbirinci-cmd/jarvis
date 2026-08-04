from __future__ import annotations

from artmach_assistant.core.code_review import CodeReviewIssue
from artmach_assistant.core.evidence_maintenance import (
    build_evidence_maintenance_report,
)
from artmach_assistant.core.runtime_observability import RuntimeFinding


def _finding(
    *,
    finding_id: str,
    count: int,
    last_seen: str,
    title: str = "Tekrarlanan hata: Example.run",
    category: str = "repeated_runtime_failure",
    path: str = "core/example.py",
    symbol: str = "Example.run",
) -> RuntimeFinding:
    return RuntimeFinding(
        finding_id=finding_id,
        severity="high",
        category=category,
        title=title,
        explanation="Ayni hata tekrarlandi.",
        confidence=0.90,
        occurrence_count=count,
        last_seen=last_seen,
        workspace="C:/repo",
        scope="own_code",
        affected_paths=(path,),
        affected_symbols=(symbol,),
        evidence=(),
        recommendation="Hedefli onarim hazirla.",
        acceptance_criteria=("Hata tekrar etmemeli.",),
        research_query="",
    )


def test_test_only_security_issue_is_excluded_from_production_report() -> None:
    issue = CodeReviewIssue(
        kind="SECURITY",
        path="tests/test_eval_example.py",
        line=10,
        message="Dinamik kod calistirma kullanimi",
        severity="high",
    )

    report = build_evidence_maintenance_report(
        (issue,),
        (),
    )

    assert report.findings == ()
    assert report.class_counts == {
        "A": 0,
        "B": 0,
        "C": 0,
    }
    assert report.repair_candidate_count == 0


def test_nested_test_directory_is_excluded() -> None:
    issue = CodeReviewIssue(
        kind="SYNTAX",
        path="plugins/example/tests/test_fixture.py",
        line=1,
        message="fixture parse example",
        severity="high",
    )

    report = build_evidence_maintenance_report(
        (issue,),
        (),
    )

    assert report.findings == ()


def test_duplicate_runtime_findings_are_aggregated() -> None:
    report = build_evidence_maintenance_report(
        (),
        (
            _finding(
                finding_id="RUN-AAAAAAAAAA",
                count=20,
                last_seen="2026-08-04T10:00:00+00:00",
            ),
            _finding(
                finding_id="RUN-BBBBBBBBBB",
                count=3,
                last_seen="2026-08-04T11:00:00+00:00",
            ),
        ),
    )

    assert len(report.findings) == 1

    finding = report.findings[0]

    assert finding.classification == "A"
    assert "23 tekrar" in finding.evidence
    assert "2026-08-04T11:00:00+00:00" in finding.evidence


def test_different_symbols_are_not_aggregated() -> None:
    report = build_evidence_maintenance_report(
        (),
        (
            _finding(
                finding_id="RUN-AAAAAAAAAA",
                count=2,
                last_seen="2026-08-04T10:00:00+00:00",
                symbol="Example.run",
            ),
            _finding(
                finding_id="RUN-BBBBBBBBBB",
                count=2,
                last_seen="2026-08-04T10:01:00+00:00",
                symbol="Example.stop",
            ),
        ),
    )

    assert len(report.findings) == 2
