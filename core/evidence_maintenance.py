from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from artmach_assistant.core.code_review import CodeReviewIssue
from artmach_assistant.core.runtime_observability import RuntimeFinding


_CLASS_ORDER = {"A": 0, "B": 1, "C": 2}


@dataclass(frozen=True, slots=True)
class EvidenceMaintenanceFinding:
    classification: str
    score: int
    source: str
    title: str
    path: str = ""
    symbol: str = ""
    evidence: str = ""
    repair_candidate: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceMaintenanceReport:
    findings: tuple[EvidenceMaintenanceFinding, ...]

    @property
    def class_counts(self) -> dict[str, int]:
        return {
            key: sum(
                1
                for finding in self.findings
                if finding.classification == key
            )
            for key in ("A", "B", "C")
        }

    @property
    def repair_candidate_count(self) -> int:
        return sum(
            1
            for finding in self.findings
            if finding.repair_candidate
        )

    def report(self, *, limit: int = 12) -> str:
        counts = self.class_counts
        rows = [
            "KANITA DAYALI SISTEM SAGLIK RAPORU",
            (
                f"A-gercek hata/guvenlik: {counts['A']} | "
                f"B-kanitli teknik borc: {counts['B']} | "
                f"C-statik inceleme ipucu: {counts['C']} | "
                f"onarim adayi: {self.repair_candidate_count}"
            ),
        ]

        for finding in self.findings[:max(1, int(limit))]:
            location = finding.path or "dosya baglantisi yok"
            if finding.symbol:
                location += f" - {finding.symbol}"
            rows.append(
                f"[{finding.classification}] Risk {finding.score} - "
                f"{finding.title}\n"
                f"Konum: {location}\n"
                f"Kanit: {finding.evidence or finding.source}\n"
                f"Otomatik onarim adayi: "
                f"{'evet' if finding.repair_candidate else 'hayir'}"
            )

        return "\n\n".join(rows)


def _runtime_score(finding: RuntimeFinding) -> int:
    severity = {
        "critical": 50,
        "high": 40,
        "medium": 25,
        "low": 10,
    }.get(finding.severity, 10)

    category = {
        "repeated_runtime_failure": 40,
        "runtime_failure": 35,
        "repeated_slow_operation": 20,
        "repeated_runtime_warning": 10,
        "runtime_warning": 5,
        "repeated_cancellation": 5,
    }.get(finding.category, 10)

    target_bonus = 15 if (
        finding.affected_paths
        and finding.affected_symbols
    ) else 0

    repeat_bonus = min(20, max(0, finding.occurrence_count - 1) * 2)

    return min(
        100,
        severity + category + target_bonus + repeat_bonus,
    )


def _runtime_classification(finding: RuntimeFinding) -> str:
    if finding.category in {
        "repeated_runtime_failure",
        "runtime_failure",
    }:
        return "A"

    if (
        finding.category == "repeated_slow_operation"
        and finding.occurrence_count >= 5
        and finding.affected_paths
        and finding.affected_symbols
    ):
        return "B"

    return "C"


def _static_classification(
    issue: CodeReviewIssue,
    runtime_paths: set[str],
) -> str:
    normalized_path = issue.path.replace("\\", "/").casefold()

    if issue.kind in {"SYNTAX", "SECURITY"}:
        return "A"

    if normalized_path in runtime_paths and issue.kind in {
        "QUALITY",
        "COMPLEXITY",
        "DUPLICATE",
    }:
        return "B"

    return "C"


def build_evidence_maintenance_report(
    static_issues: Iterable[CodeReviewIssue],
    runtime_findings: Iterable[RuntimeFinding],
) -> EvidenceMaintenanceReport:
    runtime_rows = tuple(runtime_findings)
    runtime_paths = {
        str(path).replace("\\", "/").casefold()
        for finding in runtime_rows
        for path in finding.affected_paths
    }

    findings: list[EvidenceMaintenanceFinding] = []

    for finding in runtime_rows:
        classification = _runtime_classification(finding)
        path = finding.affected_paths[0] if finding.affected_paths else ""
        symbol = (
            finding.affected_symbols[0]
            if finding.affected_symbols
            else ""
        )
        repair_candidate = bool(
            classification in {"A", "B"}
            and finding.affected_paths
            and finding.affected_symbols
            and finding.category not in {
                "repeated_runtime_warning",
                "runtime_warning",
                "repeated_cancellation",
            }
        )
        findings.append(
            EvidenceMaintenanceFinding(
                classification=classification,
                score=_runtime_score(finding),
                source="runtime",
                title=finding.title,
                path=path,
                symbol=symbol,
                evidence=(
                    f"{finding.occurrence_count} tekrar; "
                    f"guven={finding.confidence:.2f}; "
                    f"son olay={finding.last_seen}"
                ),
                repair_candidate=repair_candidate,
            )
        )

    for issue in static_issues:
        classification = _static_classification(
            issue,
            runtime_paths,
        )
        base_score = {
            "A": 70,
            "B": 40,
            "C": 10,
        }[classification]
        findings.append(
            EvidenceMaintenanceFinding(
                classification=classification,
                score=base_score,
                source="static",
                title=f"[{issue.kind}] {issue.message}",
                path=issue.path,
                evidence=(
                    "runtime ile eslesti"
                    if classification == "B"
                    else "yalnizca statik tarama"
                ),
                repair_candidate=classification == "A",
            )
        )

    findings.sort(
        key=lambda item: (
            _CLASS_ORDER[item.classification],
            -item.score,
            item.path.casefold(),
            item.title.casefold(),
        )
    )

    return EvidenceMaintenanceReport(tuple(findings))
