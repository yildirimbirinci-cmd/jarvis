from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from artmach_assistant.core.code_review import CodeReviewIssue
from artmach_assistant.core.runtime_observability import RuntimeFinding
from artmach_assistant.core.evidence_lifecycle import ACTIVE, NEEDS_RETEST, SourceLifecycleResolver


_CLASS_ORDER = {"A": 0, "B": 1, "C": 2}

RESOLVED_CANDIDATE = "RESOLVED_CANDIDATE"

_LIFECYCLE_ORDER = {
    ACTIVE: 0,
    NEEDS_RETEST: 1,
    RESOLVED_CANDIDATE: 2,
    "STATIC": 3,
}


@dataclass(frozen=True, slots=True)
class EvidenceMaintenanceFinding:
    classification: str
    score: int
    source: str
    title: str
    finding_id: str = ""
    path: str = ""
    symbol: str = ""
    evidence: str = ""
    repair_candidate: bool = False
    lifecycle: str = ACTIVE


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
    def lifecycle_counts(self) -> dict[str, int]:
        return {
            key: sum(
                1
                for finding in self.findings
                if finding.lifecycle == key
            )
            for key in (
                ACTIVE,
                NEEDS_RETEST,
            )
        }

    @property
    def resolved_candidate_count(self) -> int:
        return sum(
            1
            for finding in self.findings
            if finding.lifecycle == RESOLVED_CANDIDATE
        )

    @property
    def repair_candidate_count(self) -> int:
        return sum(
            1
            for finding in self.findings
            if finding.repair_candidate
        )

    def report(self, *, limit: int = 12) -> str:
        counts = self.class_counts
        lifecycle = self.lifecycle_counts
        rows = [
            "KANITA DAYALI SISTEM SAGLIK RAPORU",
            (
                f"A-gercek hata/guvenlik: {counts['A']} | "
                f"B-kanitli teknik borc: {counts['B']} | "
                f"C-statik inceleme ipucu: {counts['C']} | "
                f"onarim adayi: {self.repair_candidate_count}"
            ),
            (
                f"Aktif runtime bulgusu: {lifecycle[ACTIVE]} | "
                f"yeniden test edilmeli: "
                f"{lifecycle[NEEDS_RETEST]} | "
                f"cozulmus aday: "
                f"{self.resolved_candidate_count}"
            ),
        ]

        for finding in self.findings[:max(1, int(limit))]:
            location = finding.path or "dosya baglantisi yok"
            if finding.symbol:
                location += f" - {finding.symbol}"

            rows.append(
                f"[{finding.classification}] Risk {finding.score} - "
                f"{finding.title}\n"
                f"Durum: {finding.lifecycle}\n"
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


def _normalized_path(value: str) -> str:
    return str(value or "").replace("\\", "/").casefold().strip()


def _is_test_path(value: str) -> bool:
    normalized = _normalized_path(value)
    name = normalized.rsplit("/", 1)[-1]

    return bool(
        normalized.startswith("tests/")
        or "/tests/" in f"/{normalized}"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _static_issue_symbol(
    issue: CodeReviewIssue,
) -> str:
    if issue.kind != "COMPLEXITY":
        return ""

    symbol, separator, _detail = str(
        issue.message
    ).partition(":")

    if not separator:
        return ""

    return symbol.strip().casefold()


def _static_classification(
    issue: CodeReviewIssue,
    runtime_symbols_by_path: dict[str, set[str]],
) -> str:
    normalized_path = _normalized_path(issue.path)

    if issue.kind in {"SYNTAX", "SECURITY"}:
        return "A"

    if issue.kind != "COMPLEXITY":
        return "C"

    issue_symbol = _static_issue_symbol(issue)
    if not issue_symbol:
        return "C"

    runtime_symbols = runtime_symbols_by_path.get(
        normalized_path,
        set(),
    )

    matches_runtime_symbol = any(
        runtime_symbol == issue_symbol
        or runtime_symbol.endswith("." + issue_symbol)
        for runtime_symbol in runtime_symbols
    )

    return "B" if matches_runtime_symbol else "C"


_SEVERITY_ORDER = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _runtime_group_key(
    finding: RuntimeFinding,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    return (
        str(finding.category or "").casefold(),
        str(finding.title or "").casefold().strip(),
        tuple(
            sorted(
                {
                    _normalized_path(path)
                    for path in finding.affected_paths
                    if _normalized_path(path)
                }
            )
        ),
        tuple(
            sorted(
                {
                    str(symbol).casefold().strip()
                    for symbol in finding.affected_symbols
                    if str(symbol).strip()
                }
            )
        ),
    )


def _merge_runtime_findings(
    findings: Iterable[RuntimeFinding],
) -> tuple[RuntimeFinding, ...]:
    grouped: dict[
        tuple[str, str, tuple[str, ...], tuple[str, ...]],
        list[RuntimeFinding],
    ] = {}

    for finding in findings:
        grouped.setdefault(
            _runtime_group_key(finding),
            [],
        ).append(finding)

    merged: list[RuntimeFinding] = []

    for rows in grouped.values():
        primary = max(
            rows,
            key=lambda item: (
                _SEVERITY_ORDER.get(item.severity, 0),
                item.confidence,
                item.last_seen,
            ),
        )
        latest = max(
            rows,
            key=lambda item: item.last_seen,
        )

        paths = tuple(
            dict.fromkeys(
                path
                for row in rows
                for path in row.affected_paths
                if str(path).strip()
            )
        )
        symbols = tuple(
            dict.fromkeys(
                symbol
                for row in rows
                for symbol in row.affected_symbols
                if str(symbol).strip()
            )
        )
        evidence = tuple(
            dict.fromkeys(
                item
                for row in rows
                for item in row.evidence
            )
        )[:12]

        merged.append(
            RuntimeFinding(
                finding_id=primary.finding_id,
                severity=primary.severity,
                category=primary.category,
                title=primary.title,
                explanation=primary.explanation,
                confidence=max(row.confidence for row in rows),
                occurrence_count=sum(
                    max(0, int(row.occurrence_count))
                    for row in rows
                ),
                last_seen=latest.last_seen,
                workspace=primary.workspace,
                scope=primary.scope,
                affected_paths=paths,
                affected_symbols=symbols,
                evidence=evidence,
                recommendation=primary.recommendation,
                acceptance_criteria=primary.acceptance_criteria,
                research_query=primary.research_query,
            )
        )

    return tuple(merged)


def build_evidence_maintenance_report(
    static_issues: Iterable[CodeReviewIssue],
    runtime_findings: Iterable[RuntimeFinding],
    *,
    source_root: str | Path | None = None,
) -> EvidenceMaintenanceReport:
    runtime_rows = _merge_runtime_findings(
        runtime_findings
    )
    lifecycle_resolver = (
        SourceLifecycleResolver(source_root)
        if source_root is not None
        else None
    )

    runtime_symbols_by_path: dict[
        str,
        set[str],
    ] = {}

    for finding in runtime_rows:
        normalized_symbols = {
            str(symbol).casefold().strip()
            for symbol in finding.affected_symbols
            if str(symbol).strip()
        }

        for affected_path in finding.affected_paths:
            normalized_path = _normalized_path(
                affected_path
            )
            if not normalized_path:
                continue

            runtime_symbols_by_path.setdefault(
                normalized_path,
                set(),
            ).update(normalized_symbols)

    findings: list[
        EvidenceMaintenanceFinding
    ] = []

    for finding in runtime_rows:
        classification = _runtime_classification(
            finding
        )
        lifecycle = (
            lifecycle_resolver.classify(finding)
            if lifecycle_resolver is not None
            else ACTIVE
        )
        path = (
            finding.affected_paths[0]
            if finding.affected_paths
            else ""
        )
        symbol = (
            finding.affected_symbols[0]
            if finding.affected_symbols
            else ""
        )
        repair_candidate = bool(
            lifecycle == ACTIVE
            and classification in {"A", "B"}
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
                finding_id=finding.finding_id,
                path=path,
                symbol=symbol,
                evidence=(
                    f"{finding.occurrence_count} tekrar; "
                    f"guven={finding.confidence:.2f}; "
                    f"son olay={finding.last_seen}"
                ),
                repair_candidate=repair_candidate,
                lifecycle=lifecycle,
            )
        )

    for issue in static_issues:
        if _is_test_path(issue.path):
            continue

        classification = _static_classification(
            issue,
            runtime_symbols_by_path,
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
                title=(
                    f"[{issue.kind}] "
                    f"{issue.message}"
                ),
                path=issue.path,
                evidence=(
                    "runtime dosya ve sembol "
                    "kanitiyla eslesti"
                    if classification == "B"
                    else "yalnizca statik tarama"
                ),
                repair_candidate=(
                    classification == "A"
                ),
                lifecycle="STATIC",
            )
        )

    findings.sort(
        key=lambda item: (
            _LIFECYCLE_ORDER.get(
                item.lifecycle,
                4,
            ),
            _CLASS_ORDER[item.classification],
            -item.score,
            item.path.casefold(),
            item.title.casefold(),
        )
    )

    return EvidenceMaintenanceReport(
        tuple(findings)
    )
