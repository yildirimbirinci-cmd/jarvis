from __future__ import annotations

from artmach_assistant.core.code_review import CodeReviewIssue
from artmach_assistant.core.evidence_maintenance import (
    build_evidence_maintenance_report,
)
from artmach_assistant.core.runtime_observability import RuntimeFinding


def _runtime(symbol: str) -> RuntimeFinding:
    return RuntimeFinding(
        finding_id="RUN-ABCDEF1234",
        severity="high",
        category="repeated_runtime_failure",
        title="Tekrarlanan hata",
        explanation="Runtime hatasi.",
        confidence=0.95,
        occurrence_count=4,
        last_seen="2026-08-04T10:00:00+00:00",
        workspace="C:/repo",
        scope="own_code",
        affected_paths=("core/assistant.py",),
        affected_symbols=(symbol,),
        evidence=(),
        recommendation="Incele.",
        acceptance_criteria=(
            "Hata tekrar etmemeli.",
        ),
        research_query="",
    )


def _complexity(
    symbol: str,
) -> CodeReviewIssue:
    return CodeReviewIssue(
        kind="COMPLEXITY",
        path="core/assistant.py",
        line=100,
        message=(
            f"{symbol}: "
            "80 satirdan uzun fonksiyon"
        ),
        severity="medium",
    )


def _static_finding(report):
    return next(
        finding
        for finding in report.findings
        if finding.source == "static"
    )


def test_same_file_different_symbol_stays_class_c() -> None:
    report = build_evidence_maintenance_report(
        (_complexity("__init__"),),
        (
            _runtime(
                "AssistantEngine."
                "prepare_own_code_proposal"
            ),
        ),
    )

    finding = _static_finding(report)

    assert finding.classification == "C"
    assert finding.repair_candidate is False
    assert finding.evidence == (
        "yalnizca statik tarama"
    )


def test_same_file_matching_symbol_becomes_class_b() -> None:
    report = build_evidence_maintenance_report(
        (
            _complexity(
                "prepare_own_code_proposal"
            ),
        ),
        (
            _runtime(
                "AssistantEngine."
                "prepare_own_code_proposal"
            ),
        ),
    )

    finding = _static_finding(report)

    assert finding.classification == "B"
    assert finding.repair_candidate is False
    assert finding.evidence == (
        "runtime dosya ve sembol "
        "kanitiyla eslesti"
    )


def test_unrelated_file_stays_class_c() -> None:
    issue = CodeReviewIssue(
        kind="COMPLEXITY",
        path="app.py",
        line=10,
        message=(
            "main: 80 satirdan uzun fonksiyon"
        ),
        severity="medium",
    )

    report = build_evidence_maintenance_report(
        (issue,),
        (
            _runtime(
                "AssistantEngine."
                "prepare_own_code_proposal"
            ),
        ),
    )

    assert (
        _static_finding(report).classification
        == "C"
    )
