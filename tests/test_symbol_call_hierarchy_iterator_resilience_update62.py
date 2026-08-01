from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SymbolRecord:
    path: str
    line: int
    column: int
    name: str
    qualified_name: str
    end_line: int = 1
    kind: str = "function"


@dataclass
class SymbolReferenceRecord:
    path: str
    line: int
    column: int
    scope: str | None = None
    context: str = "call"


@dataclass
class CallGraphEdge:
    caller_path: str
    call_line: int
    call_column: int
    callee_canonical_name: str
    callee_path: str


class ExplodingIterator:
    def __init__(self, first):
        self.first = first
        self.count = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.count == 0:
            self.count += 1
            return self.first
        raise RuntimeError("stale index page")


def _load_module():
    package = types.ModuleType("artmach_assistant")
    core = types.ModuleType("artmach_assistant.core")
    validation = types.ModuleType("artmach_assistant.core.query_validation")
    indexing = types.ModuleType("artmach_assistant.indexing")

    validation.normalized_query = lambda value: str(value).strip()
    validation.bounded_positive_int = lambda value, default, maximum: min(max(int(value), 1), maximum)

    indexing.CallGraph = object
    indexing.CallGraphEdge = CallGraphEdge
    indexing.CrossFileReferenceResolver = object
    indexing.SymbolIndex = object
    indexing.SymbolRecord = SymbolRecord
    indexing.SymbolReferenceIndex = object
    indexing.SymbolReferenceRecord = SymbolReferenceRecord

    sys.modules["artmach_assistant"] = package
    sys.modules["artmach_assistant.core"] = core
    sys.modules["artmach_assistant.core.query_validation"] = validation
    sys.modules["artmach_assistant.indexing"] = indexing

    path = Path(__file__).parents[1] / "core" / "symbol_call_hierarchy_service.py"
    spec = importlib.util.spec_from_file_location("update62_symbol_call_hierarchy_service", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_safe_iter_preserves_records_before_iterator_failure():
    module = _load_module()
    assert tuple(module.SymbolCallHierarchyService._safe_iter(ExplodingIterator("first"))) == ("first",)


def test_call_reference_filter_survives_mid_iteration_failure():
    module = _load_module()
    record = SymbolReferenceRecord("sample.py", 4, 2, context="call")
    result = module.SymbolCallHierarchyService._call_references(
        ExplodingIterator(record), limit=10
    )
    assert result == (record,)


def test_find_enclosing_symbol_ignores_runtime_broken_record():
    module = _load_module()

    class BrokenSymbol:
        @property
        def line(self):
            raise RuntimeError("stale symbol")

    reference = SymbolReferenceRecord("sample.py", 8, 1)
    valid = SymbolRecord("sample.py", 3, 0, "run", "run", end_line=10)
    assert module.SymbolCallHierarchyService._find_enclosing_symbol(
        reference, (BrokenSymbol(), valid)
    ) is valid
