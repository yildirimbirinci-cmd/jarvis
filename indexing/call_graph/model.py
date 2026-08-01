"""Immutable call-graph records."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class CallSite:
    path: str
    line: int
    column: int
    expression: str
    caller_qualified_name: str | None
    scope: str | None


@dataclass(frozen=True, slots=True)
class CallGraphEdge:
    caller_canonical_name: str | None
    caller_path: str
    caller_line: int
    callee_canonical_name: str
    callee_path: str
    callee_line: int
    call_expression: str
    call_line: int
    call_column: int
    confidence: float


@dataclass(frozen=True, slots=True)
class CallGraphBuildResult:
    path: str
    call_sites: tuple[CallSite, ...]
    edges: tuple[CallGraphEdge, ...]
    unresolved_calls: int = 0
    ambiguous_calls: int = 0
    parse_error: str | None = None

    def __post_init__(self) -> None:
        # Keep externally reconstructed records deterministic and immutable.
        object.__setattr__(self, "path", _safe_text(self.path))
        object.__setattr__(self, "call_sites", _safe_record_tuple(self.call_sites, CallSite))
        object.__setattr__(self, "edges", _safe_record_tuple(self.edges, CallGraphEdge))
        object.__setattr__(self, "unresolved_calls", _safe_non_negative_int(self.unresolved_calls))
        object.__setattr__(self, "ambiguous_calls", _safe_non_negative_int(self.ambiguous_calls))
        object.__setattr__(self, "parse_error", _safe_optional_text(self.parse_error))


@dataclass(frozen=True, slots=True)
class CallGraphFileDiagnostics:
    """Latest non-executable analysis quality data for one source file."""

    path: str
    call_sites: int
    resolved_calls: int
    unresolved_calls: int
    ambiguous_calls: int
    parse_error: str | None = None

    @property
    def resolution_rate(self) -> float:
        # Ambiguous calls still have one or more resolved targets, so they are
        # a subset of resolved_calls rather than a third mutually exclusive
        # bucket. Only unresolved calls reduce target-resolution coverage.
        return _safe_resolution_rate(self.resolved_calls, self.unresolved_calls)


@dataclass(frozen=True, slots=True)
class CallGraphDiagnosticsReport:
    """Deterministic project-wide call-graph coverage report."""

    files: tuple[CallGraphFileDiagnostics, ...]
    total_call_sites: int
    resolved_calls: int
    unresolved_calls: int
    ambiguous_calls: int
    parse_errors: int

    @property
    def resolution_rate(self) -> float:
        # Ambiguous calls still have one or more resolved targets, so they are
        # a subset of resolved_calls rather than a third mutually exclusive
        # bucket. Only unresolved calls reduce target-resolution coverage.
        return _safe_resolution_rate(self.resolved_calls, self.unresolved_calls)


@dataclass(frozen=True, slots=True)
class CallGraphRecursionComponent:
    """One strongly connected recursive symbol group."""

    symbols: tuple[str, ...]
    edges: tuple[CallGraphEdge, ...]
    is_direct: bool = False


@dataclass(frozen=True, slots=True)
class CallGraphRecursionReport:
    """Bounded project-wide recursion analysis result."""

    components: tuple[CallGraphRecursionComponent, ...]
    total_components: int
    recursive_symbols: int
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class CallGraphPath:
    """One deterministic multi-hop route through the resolved call graph."""

    symbols: tuple[str, ...]
    edges: tuple[CallGraphEdge, ...]
    is_cycle: bool = False


@dataclass(frozen=True, slots=True)
class CallGraphTraversalResult:
    """Bounded traversal result for callers or callees."""

    root: str
    direction: str
    max_depth: int
    paths: tuple[CallGraphPath, ...]
    truncated: bool = False

    @property
    def cycles(self) -> tuple[CallGraphPath, ...]:
        return tuple(path for path in self.paths if path.is_cycle)

@dataclass(frozen=True, slots=True)
class CallGraphHotspot:
    """One high-impact symbol ranked by incoming and outgoing call pressure."""

    canonical_name: str
    incoming_calls: int
    outgoing_calls: int
    caller_files: int
    callee_files: int
    score: float
    recursive: bool = False


@dataclass(frozen=True, slots=True)
class CallGraphHotspotReport:
    """Bounded deterministic call-graph hotspot analysis result."""

    hotspots: tuple[CallGraphHotspot, ...]
    total_symbols: int
    truncated: bool = False

@dataclass(frozen=True, slots=True)
class CallGraphReachabilityReport:
    """Bounded symbol reachability analysis from explicit or inferred entry points."""

    entry_points: tuple[str, ...]
    reachable_symbols: tuple[str, ...]
    unreachable_symbols: tuple[str, ...]
    total_symbols: int
    truncated: bool = False



def _safe_resolution_rate(resolved: object, unresolved: object) -> float:
    """Return a finite coverage ratio even for malformed external records."""
    try:
        resolved_value = float(resolved)
        unresolved_value = float(unresolved)
    except (TypeError, ValueError, OverflowError):
        return 1.0
    if not math.isfinite(resolved_value) or not math.isfinite(unresolved_value):
        return 1.0
    resolved_value = max(0.0, resolved_value)
    unresolved_value = max(0.0, unresolved_value)
    total = resolved_value + unresolved_value
    if total <= 0.0 or not math.isfinite(total):
        return 1.0
    return min(1.0, max(0.0, resolved_value / total))


def _safe_non_negative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_record_tuple(value: object, expected_type: type) -> tuple:
    """Materialize external record collections without leaking iterator failures."""
    if isinstance(value, (str, bytes)) or value is None:
        return ()
    try:
        return tuple(item for item in value if isinstance(item, expected_type))
    except (TypeError, RuntimeError, ValueError, OSError, MemoryError):
        return ()


def _safe_text(value: object) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _safe_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = _safe_text(value).strip()
    return text or None
