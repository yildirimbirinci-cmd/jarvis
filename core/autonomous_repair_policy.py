from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

AUTO_ALLOWED = "AUTO_ALLOWED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
BLOCKED_HIGH_RISK = "BLOCKED_HIGH_RISK"
BLOCKED_PROTECTED_TARGET = "BLOCKED_PROTECTED_TARGET"
BLOCKED_INSUFFICIENT_EVIDENCE = "BLOCKED_INSUFFICIENT_EVIDENCE"
BLOCKED_NEEDS_FRESH_EVIDENCE = "BLOCKED_NEEDS_FRESH_EVIDENCE"
BLOCKED_WRONG_TARGET = "BLOCKED_WRONG_TARGET"

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

_HARD_BLOCK_SENSITIVE_PARTS = {
    "auth",
    "authorization",
    "credential",
    "credentials",
    "crypto",
    "firewall",
    "permission",
    "security",
    "secret",
    "secrets",
}

_REPAIRABLE_RUNTIME_ERRORS = {
    "runtimeerror",
    "typeerror",
    "attributeerror",
    "nameerror",
    "importerror",
    "modulenotfounderror",
}

_PROTECTED_PATHS = {
    "core/autonomous_repair_policy.py",
    "core/approval_gate.py",
    "core/delegated_approval.py",
    "core/own_code_authority.py",
    "core/own_code_security_guard.py",
    "core/trust_inbox.py",
}

_PROTECTED_PREFIXES = (
    "core/constitution/",
)

_PROTECTED_SYMBOL_PREFIXES = (
    "AgentPolicy",
    "ApprovalGate",
    "AutonomousRepairDecision",
    "Constitution",
    "ConstitutionRegistry",
    "MemoryPolicy",
    "PlanningPolicy",
    "RuntimePolicy",
    "SecurityGuardResult",
)

_ELIGIBLE_CATEGORIES = {
    "repeated_runtime_failure",
    "runtime_failure",
    "repeated_slow_operation",
}


@dataclass(frozen=True, slots=True)
class AutonomousRepairDecision:
    status: str
    risk: str
    reason: str
    approved_paths: tuple[str, ...]
    approved_symbols: tuple[str, ...]
    max_attempts: int = 0

    @property
    def allowed(self) -> bool:
        return self.status == AUTO_ALLOWED

    @property
    def approval_required(self) -> bool:
        return self.status == APPROVAL_REQUIRED

    @property
    def can_prepare_plan(self) -> bool:
        return self.status in {AUTO_ALLOWED, APPROVAL_REQUIRED}

    def report(self) -> str:
        paths = ", ".join(self.approved_paths) or "none"
        symbols = ", ".join(self.approved_symbols) or "none"
        return (
            "AUTONOMOUS REPAIR POLICY\n"
            f"Status: {self.status}\n"
            f"Risk: {self.risk}\n"
            f"Reason: {self.reason}\n"
            f"Transformation limit: {self.max_attempts}\n"
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
    parts = {part.casefold() for part in PurePosixPath(path).parts}
    stem_parts = {
        token
        for part in parts
        for token in part.replace("-", "_").split("_")
    }
    return bool((parts | stem_parts) & _SENSITIVE_PARTS)


def _hard_block_sensitive_path(path: str) -> bool:
    parts = {part.casefold() for part in PurePosixPath(path).parts}
    stem_parts = {
        token
        for part in parts
        for token in part.replace("-", "_").split("_")
    }
    return bool((parts | stem_parts) & _HARD_BLOCK_SENSITIVE_PARTS)


def _runtime_error_type(finding: object) -> str:
    explicit = str(getattr(finding, "error_type", "") or "").strip().casefold()
    if explicit:
        return explicit
    evidence_details = tuple(
        str(getattr(item, "detail", "") or "")
        for item in tuple(getattr(finding, "evidence", ()) or ())
    )
    haystack = " ".join(
        (
            str(getattr(finding, "title", "") or ""),
            str(getattr(finding, "explanation", "") or ""),
            str(getattr(finding, "research_query", "") or ""),
            *evidence_details,
        )
    ).casefold()
    for error_type in _REPAIRABLE_RUNTIME_ERRORS:
        if error_type in haystack:
            return error_type
    return ""


def _protected_path(path: str) -> bool:
    key = PurePosixPath(path).as_posix().casefold()
    if key in _PROTECTED_PATHS:
        return True
    return any(key.startswith(prefix) for prefix in _PROTECTED_PREFIXES)


def _protected_symbol(symbol: str) -> bool:
    key = str(symbol or "").strip().casefold()
    return any(
        key == prefix.casefold() or key.startswith(prefix.casefold() + ".")
        for prefix in _PROTECTED_SYMBOL_PREFIXES
    )


def _stage_timing_summary(finding: object) -> tuple[int, float, float]:
    rows = []
    for item in tuple(getattr(finding, "evidence", ()) or ()):
        action_ms = float(getattr(item, "action_duration_ms", 0.0) or 0.0)
        wrapper_ms = float(getattr(item, "wrapper_overhead_ms", 0.0) or 0.0)
        completed = bool(getattr(item, "action_completed", False))
        if completed and (action_ms > 0.0 or wrapper_ms > 0.0):
            rows.append((action_ms, wrapper_ms))
    if not rows:
        return 0, 0.0, 0.0
    action_values = sorted(row[0] for row in rows)
    wrapper_values = sorted(row[1] for row in rows)
    middle = len(rows) // 2
    return len(rows), action_values[middle], wrapper_values[middle]


def _decision(
    status: str,
    risk: str,
    reason: str,
    paths: tuple[str, ...],
    symbols: tuple[str, ...],
    max_attempts: int = 0,
) -> AutonomousRepairDecision:
    return AutonomousRepairDecision(
        status=status,
        risk=risk,
        reason=reason,
        approved_paths=paths,
        approved_symbols=symbols,
        max_attempts=0 if int(max_attempts) <= 0 else 1,
    )


def assess_autonomous_runtime_repair(finding: object) -> AutonomousRepairDecision:
    paths = _normalise_paths(getattr(finding, "affected_paths", ()))
    symbols = _normalise_symbols(getattr(finding, "affected_symbols", ()))
    category = str(getattr(finding, "category", "") or "").casefold()
    severity = str(getattr(finding, "severity", "") or "").casefold()
    confidence = float(getattr(finding, "confidence", 0.0) or 0.0)
    occurrences = int(getattr(finding, "occurrence_count", 0) or 0)
    production_paths = tuple(
        path
        for path in paths
        if not path.casefold().startswith("tests/")
        and "/tests/" not in path.casefold()
    )

    if not production_paths or not symbols:
        return _decision(
            BLOCKED_INSUFFICIENT_EVIDENCE,
            "UNKNOWN",
            "Exact production path and symbol evidence is required.",
            production_paths,
            symbols,
        )
    if confidence < 0.75 or occurrences < 3:
        return _decision(
            BLOCKED_INSUFFICIENT_EVIDENCE,
            "UNKNOWN",
            "Confidence or occurrence evidence is below the autonomous threshold.",
            production_paths,
            symbols,
        )
    if category not in _ELIGIBLE_CATEGORIES:
        return _decision(
            BLOCKED_INSUFFICIENT_EVIDENCE,
            "UNKNOWN",
            "This finding category is not eligible for autonomous source changes.",
            production_paths,
            symbols,
        )
    if any(_protected_path(path) for path in production_paths) or any(
        _protected_symbol(symbol) for symbol in symbols
    ):
        return _decision(
            BLOCKED_PROTECTED_TARGET,
            "CRITICAL",
            "The target belongs to Jarvis trust, approval, security, or repair-policy control code and cannot be changed autonomously.",
            production_paths,
            symbols,
        )
    if any(_hard_block_sensitive_path(path) for path in production_paths):
        return _decision(
            BLOCKED_HIGH_RISK,
            "CRITICAL",
            "The target crosses a security, credential, authorization, or permission boundary and cannot be repaired autonomously.",
            production_paths,
            symbols,
        )
    runtime_error = _runtime_error_type(finding)
    if (
        category == "repeated_runtime_failure"
        and runtime_error in _REPAIRABLE_RUNTIME_ERRORS
        and len(production_paths) == 1
        and len(symbols) == 1
    ):
        return _decision(
            AUTO_ALLOWED,
            "LOW",
            "A repeated repairable runtime exception has one exact production path and symbol; bounded autonomous repair is permitted.",
            production_paths,
            symbols,
            1,
        )
    if category == "repeated_slow_operation" and any(
        symbol.endswith(".wrap.execute") for symbol in symbols
    ):
        sample_count, action_median, wrapper_median = _stage_timing_summary(finding)
        if sample_count < 3:
            return _decision(
                BLOCKED_NEEDS_FRESH_EVIDENCE,
                "UNKNOWN",
                "At least three fresh stage-timing samples are required before changing the wrapper.",
                production_paths,
                symbols,
            )
        if action_median > max(50.0, wrapper_median * 3.0):
            return _decision(
                BLOCKED_WRONG_TARGET,
                "UNKNOWN",
                "Fresh timing evidence shows the wrapped action dominates latency; the wrapper is not the proven bottleneck.",
                production_paths,
                symbols,
            )
    if severity == "critical" or len(production_paths) > 4:
        return _decision(
            BLOCKED_HIGH_RISK,
            "CRITICAL",
            "The target crosses the autonomous repair safety ceiling and requires manual engineering review.",
            production_paths,
            symbols,
        )

    sensitive = any(_sensitive_path(path) for path in production_paths)
    if sensitive:
        return _decision(
            APPROVAL_REQUIRED,
            "HIGH",
            "The target touches a sensitive system boundary. A plan may be prepared, but proposal generation and apply require explicit user approval.",
            production_paths,
            symbols,
            1,
        )
    if severity == "high" or len(production_paths) >= 2:
        return _decision(
            APPROVAL_REQUIRED,
            "MEDIUM" if severity != "high" else "HIGH",
            "The repair scope is valid but exceeds the low-risk autonomous envelope. Explicit user approval is required before source changes.",
            production_paths,
            symbols,
            1,
        )
    return _decision(
        AUTO_ALLOWED,
        "LOW",
        "Evidence, target scope, validation and rollback prerequisites permit autonomous repair.",
        production_paths,
        symbols,
        1,
    )

@dataclass(frozen=True, slots=True)
class AutonomousRepairEnforcement:
    allowed: bool
    reason: str


def validate_runtime_repair_enforcement(
    decision: AutonomousRepairDecision,
    *,
    stored_status: str,
    stored_risk: str,
    stored_max_attempts: int,
    approval_granted: bool,
    attempts: int,
    session_paths: Iterable[object],
    session_symbols: Iterable[object],
    proposal_paths: Iterable[object],
) -> AutonomousRepairEnforcement:
    """Revalidate persisted repair authority immediately before source apply.

    The current policy decision is authoritative. Persisted session metadata and
    the pending proposal may narrow that authority, but they may never broaden it.
    """
    if not decision.can_prepare_plan:
        return AutonomousRepairEnforcement(
            False, "Current repair policy no longer permits this target."
        )

    if str(stored_status or "") != decision.status:
        return AutonomousRepairEnforcement(
            False, "Persisted policy status does not match the current decision."
        )
    if str(stored_risk or "") != decision.risk:
        return AutonomousRepairEnforcement(
            False, "Persisted policy risk does not match the current decision."
        )
    try:
        persisted_limit = int(stored_max_attempts)
    except (TypeError, ValueError):
        persisted_limit = -1
    if persisted_limit != decision.max_attempts:
        return AutonomousRepairEnforcement(
            False, "Persisted retry limit does not match the current decision."
        )

    expected_paths = _normalise_paths(decision.approved_paths)
    saved_paths = _normalise_paths(session_paths)
    if saved_paths != expected_paths:
        return AutonomousRepairEnforcement(
            False, "Persisted repair paths do not match current policy scope."
        )

    expected_symbols = _normalise_symbols(decision.approved_symbols)
    saved_symbols = _normalise_symbols(session_symbols)
    if saved_symbols != expected_symbols:
        return AutonomousRepairEnforcement(
            False, "Persisted repair symbols do not match current policy scope."
        )

    produced_paths = _normalise_paths(proposal_paths)
    if not produced_paths:
        return AutonomousRepairEnforcement(False, "Pending proposal has no valid paths.")
    unexpected = tuple(path for path in produced_paths if path not in expected_paths)
    if unexpected:
        return AutonomousRepairEnforcement(
            False,
            "Pending proposal exceeds policy path scope: " + ", ".join(unexpected),
        )

    if decision.approval_required and not bool(approval_granted):
        return AutonomousRepairEnforcement(
            False, "Explicit approval required by the current policy is missing."
        )

    try:
        attempt_count = int(attempts)
    except (TypeError, ValueError):
        attempt_count = -1
    if decision.max_attempts <= 0:
        return AutonomousRepairEnforcement(False, "Current policy permits no repair attempts.")
    if attempt_count <= 0 or attempt_count > decision.max_attempts:
        return AutonomousRepairEnforcement(
            False, "Repair attempt count is outside the current policy limit."
        )

    return AutonomousRepairEnforcement(True, "Current policy and pending proposal scope match.")

