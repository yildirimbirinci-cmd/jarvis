from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

AUTO_ALLOWED = "AUTO_ALLOWED"
BLOCKED_HIGH_RISK = "BLOCKED_HIGH_RISK"
BLOCKED_INSUFFICIENT_EVIDENCE = "BLOCKED_INSUFFICIENT_EVIDENCE"

_SENSITIVE_PARTS = {
    "auth",
    "authorization",
    "credential",
    "credentials",
    "crypto",
    "firewall",
    "installer",
    "migration",
    "network",
    "permission",
    "registry",
    "security",
    "secret",
    "secrets",
    "service",
    "setup",
    "update",
}


@dataclass(frozen=True, slots=True)
class AutonomousRepairDecision:
    status: str
    risk: str
    reason: str
    approved_paths: tuple[str, ...]
    approved_symbols: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return self.status == AUTO_ALLOWED

    def report(self) -> str:
        paths = ", ".join(self.approved_paths) or "none"
        symbols = ", ".join(self.approved_symbols) or "none"
        return (
            "AUTONOMOUS REPAIR POLICY\n"
            f"Status: {self.status}\n"
            f"Risk: {self.risk}\n"
            f"Reason: {self.reason}\n"
            f"Paths: {paths}\n"
            f"Symbols: {symbols}"
        )


def _normalise_paths(values: Iterable[object]) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip().replace("\\", "/")
        if not cleaned or cleaned.startswith("/") or ".." in PurePosixPath(cleaned).parts:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(cleaned)
    return tuple(rows)


def _normalise_symbols(values: Iterable[object]) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split())
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        rows.append(cleaned)
    return tuple(rows)


def _sensitive_path(path: str) -> bool:
    parts = {
        part.casefold()
        for part in PurePosixPath(path).parts
    }
    stem_parts = {
        token
        for part in parts
        for token in part.replace("-", "_").split("_")
    }
    return bool((parts | stem_parts) & _SENSITIVE_PARTS)


def assess_autonomous_runtime_repair(finding: object) -> AutonomousRepairDecision:
    paths = _normalise_paths(getattr(finding, "affected_paths", ()))
    symbols = _normalise_symbols(getattr(finding, "affected_symbols", ()))
    category = str(getattr(finding, "category", "") or "").casefold()
    severity = str(getattr(finding, "severity", "") or "").casefold()
    confidence = float(getattr(finding, "confidence", 0.0) or 0.0)
    occurrences = int(getattr(finding, "occurrence_count", 0) or 0)

    production_paths = tuple(
        path for path in paths
        if not path.casefold().startswith("tests/")
        and "/tests/" not in path.casefold()
    )

    if not production_paths or not symbols:
        return AutonomousRepairDecision(
            BLOCKED_INSUFFICIENT_EVIDENCE,
            "UNKNOWN",
            "Exact production path and symbol evidence is required.",
            production_paths,
            symbols,
        )

    if confidence < 0.75 or occurrences < 3:
        return AutonomousRepairDecision(
            BLOCKED_INSUFFICIENT_EVIDENCE,
            "UNKNOWN",
            "Confidence or occurrence evidence is below the autonomous threshold.",
            production_paths,
            symbols,
        )

    if category not in {
        "repeated_runtime_failure",
        "runtime_failure",
        "repeated_slow_operation",
    }:
        return AutonomousRepairDecision(
            BLOCKED_INSUFFICIENT_EVIDENCE,
            "UNKNOWN",
            "This finding category is not eligible for autonomous source changes.",
            production_paths,
            symbols,
        )

    high_risk = (
        severity == "critical"
        or len(production_paths) > 2
        or any(_sensitive_path(path) for path in production_paths)
    )
    if high_risk:
        return AutonomousRepairDecision(
            BLOCKED_HIGH_RISK,
            "HIGH",
            "The target crosses a high-risk boundary and requires manual engineering review.",
            production_paths,
            symbols,
        )

    risk = "MEDIUM" if len(production_paths) == 2 or severity == "high" else "LOW"
    return AutonomousRepairDecision(
        AUTO_ALLOWED,
        risk,
        "Evidence, target scope, validation and rollback prerequisites permit autonomous repair.",
        production_paths,
        symbols,
    )
