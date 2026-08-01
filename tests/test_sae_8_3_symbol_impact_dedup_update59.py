from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from artmach_assistant.core.symbol_impact_analysis_service import SymbolImpactAnalysisService


@dataclass(frozen=True)
class _Reference:
    path: str
    line: int
    column: int
    context: str
    scope: str | None = None


@dataclass(frozen=True)
class _Edge:
    caller_path: str
    call_line: int
    call_column: int
    callee_canonical_name: str
    callee_path: str


class _ReferenceIndex:
    def __init__(self, values):
        self.values = values

    def references_to(self, name: str, *, limit: int):
        return self.values[:limit]


class _CallGraph:
    def __init__(self, values):
        self.values = values

    def callers(self, name: str, *, limit: int):
        return self.values[:limit]


class _Unused:
    def search(self, *args, **kwargs):
        return ()


def _service(*, references=(), edges=()):
    return SymbolImpactAnalysisService(
        Path.cwd(),
        _Unused(),
        _ReferenceIndex(list(references)),
        call_graph=_CallGraph(list(edges)),
    )


def test_unresolved_references_deduplicate_case_variants() -> None:
    upper = _Reference("PKG/Worker.py", 10, 2, "CALL", "Worker")
    lower = _Reference("pkg/worker.py", 10, 2, "call", "worker")
    service = _service(references=(upper, lower))

    result = service._unresolved_references("target", 20)

    assert result == (upper,)


def test_incoming_edges_deduplicate_case_variants() -> None:
    upper = _Edge("PKG/Caller.py", 7, 4, "PKG.Target", "PKG/Target.py")
    lower = _Edge("pkg/caller.py", 7, 4, "pkg.target", "pkg/target.py")
    service = _service(edges=(upper, lower))

    result = service._incoming_call_edges(("pkg.Target",), 20)

    assert result == (upper,)
