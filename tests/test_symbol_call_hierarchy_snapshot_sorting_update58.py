from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


class _Record:
    pass


class _FlakyEdge:
    def __init__(self) -> None:
        self._reads = 0

    def _value(self, value):
        self._reads += 1
        if self._reads > 5:
            raise RuntimeError("stale edge mutated during sort")
        return value

    @property
    def caller_path(self):
        return self._value("b.py")

    @property
    def call_line(self):
        return self._value(2)

    @property
    def call_column(self):
        return self._value(1)

    @property
    def callee_canonical_name(self):
        return self._value("pkg.target")

    @property
    def callee_path(self):
        return self._value("target.py")


class _StableEdge:
    caller_path = "a.py"
    call_line = 1
    call_column = 0
    callee_canonical_name = "pkg.target"
    callee_path = "target.py"


class _CallGraph:
    def callers(self, canonical_name, *, limit):
        return (_FlakyEdge(), _StableEdge())

    def callees(self, canonical_name, *, limit):
        return ()


def _load_module():
    package = types.ModuleType("artmach_assistant")
    core = types.ModuleType("artmach_assistant.core")
    validation = types.ModuleType("artmach_assistant.core.query_validation")
    validation.normalized_query = lambda value: str(value or "").strip()
    validation.bounded_positive_int = lambda value, default, maximum: max(1, min(int(value), maximum))

    indexing = types.ModuleType("artmach_assistant.indexing")
    for name in (
        "CallGraph", "CallGraphEdge", "CrossFileReferenceResolver", "SymbolIndex",
        "SymbolRecord", "SymbolReferenceIndex", "SymbolReferenceRecord",
    ):
        setattr(indexing, name, _Record)

    sys.modules.update({
        "artmach_assistant": package,
        "artmach_assistant.core": core,
        "artmach_assistant.core.query_validation": validation,
        "artmach_assistant.indexing": indexing,
    })

    path = Path(__file__).resolve().parents[1] / "core" / "symbol_call_hierarchy_service.py"
    spec = importlib.util.spec_from_file_location("tested_symbol_call_hierarchy_service", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_graph_edges_use_captured_snapshot_for_sorting(tmp_path):
    module = _load_module()
    service = module.SymbolCallHierarchyService(
        tmp_path,
        symbol_index=object(),
        reference_index=object(),
        call_graph=_CallGraph(),
    )

    result = service._graph_edges(("pkg.target",), incoming=True, limit=10)

    assert len(result) == 2
    assert result[0].caller_path == "a.py"
