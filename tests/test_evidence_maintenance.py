from __future__ import annotations

from artmach_assistant.core.code_review import CodeReviewIssue
from artmach_assistant.core.evidence_maintenance import (
    build_evidence_maintenance_report,
)
from artmach_assistant.core.runtime_observability import RuntimeFinding


def _runtime_finding(
    *,
    category: str,
    severity: str,
    count: int,
    path: str = "core/example.py",
    symbol: str = "Example.run",
) -> RuntimeFinding:
    return RuntimeFinding(
        finding_id="RUN-ABCDEF1234",
        severity=severity,
        category=category,
        title="Tekrarlanan uretim hatasi",
        explanation="Kanita dayali runtime bulgusu.",
        confidence=0.95,
        occurrence_count=count,
        last_seen="2026-08-04T10:00:00+00:00",
        workspace="C:/repo",
        scope="own_code",
        affected_paths=(path,) if path else (),
        affected_symbols=(symbol,) if symbol else (),
        evidence=(),
        recommendation="Hedefli duzeltme hazirla.",
        acceptance_criteria=("Hata tekrar etmemeli.",),
        research_query="",
    )


def test_runtime_failure_is_class_a_repair_candidate() -> None:
    report = build_evidence_maintenance_report(
        (),
        (
            _runtime_finding(
                category="repeated_runtime_failure",
                severity="high",
                count=4,
            ),
        ),
    )

    finding = report.findings[0]

    assert finding.classification == "A"
    assert finding.repair_candidate is True
    assert finding.score >= 80


def test_repeated_targeted_slow_operation_is_class_b() -> None:
    report = build_evidence_maintenance_report(
        (),
        (
            _runtime_finding(
                category="repeated_slow_operation",
                severity="medium",
                count=5,
            ),
        ),
    )

    finding = report.findings[0]

    assert finding.classification == "B"
    assert finding.repair_candidate is True


def test_static_complexity_without_runtime_is_class_c() -> None:
    issue = CodeReviewIssue(
        kind="COMPLEXITY",
        path="app.py",
        line=100,
        message="run: 80 satirdan uzun fonksiyon",
        severity="medium",
    )

    report = build_evidence_maintenance_report((issue,), ())

    finding = report.findings[0]

    assert finding.classification == "C"
    assert finding.repair_candidate is False


def test_static_complexity_matching_runtime_becomes_class_b() -> None:
    issue = CodeReviewIssue(
        kind="COMPLEXITY",
        path="core/example.py",
        line=20,
        message="run: 80 satirdan uzun fonksiyon",
        severity="medium",
    )

    report = build_evidence_maintenance_report(
        (issue,),
        (
            _runtime_finding(
                category="repeated_slow_operation",
                severity="medium",
                count=6,
            ),
        ),
    )

    static_finding = next(
        finding
        for finding in report.findings
        if finding.source == "static"
    )

    assert static_finding.classification == "B"
    assert static_finding.repair_candidate is False


def test_report_includes_class_summary() -> None:
    issue = CodeReviewIssue(
        kind="STYLE",
        path="app.py",
        line=1,
        message="uzun satir",
        severity="low",
    )

    rendered = build_evidence_maintenance_report(
        (issue,),
        (),
    ).report()

    assert "A-gercek hata/guvenlik: 0" in rendered
    assert "B-kanitli teknik borc: 0" in rendered
    assert "C-statik inceleme ipucu: 1" in rendered
    assert "onarim adayi: 0" in rendered
