from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


class _ExplodingContext:
    @property
    def context(self):
        raise RuntimeError("stale reference")


class _Reference:
    def __init__(self, context: str):
        self.context = context


def _load_module():
    package = types.ModuleType("artmach_assistant")
    core = types.ModuleType("artmach_assistant.core")
    query_validation = types.ModuleType("artmach_assistant.core.query_validation")
    query_validation.bounded_positive_int = lambda value, **_: int(value)
    query_validation.normalized_query = lambda value: str(value).strip()

    indexing = types.ModuleType("artmach_assistant.indexing")
    for name in (
        "CallGraph", "CallGraphEdge", "CrossFileReferenceResolver", "SymbolIndex",
        "SymbolRecord", "SymbolReferenceIndex", "SymbolReferenceRecord",
    ):
        setattr(indexing, name, type(name, (), {}))

    sys.modules.update({
        "artmach_assistant": package,
        "artmach_assistant.core": core,
        "artmach_assistant.core.query_validation": query_validation,
        "artmach_assistant.indexing": indexing,
    })
    path = Path(__file__).parents[1] / "core" / "symbol_call_hierarchy_service.py"
    spec = importlib.util.spec_from_file_location("update61_reference_service", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_call_reference_filter_skips_stale_records_and_non_calls():
    module = _load_module()
    valid = _Reference("call")

    result = module.SymbolCallHierarchyService._call_references(
        (_ExplodingContext(), _Reference("read"), valid),
        limit=10,
    )

    assert result == (valid,)


def test_call_reference_filter_rejects_non_iterable_results():
    module = _load_module()

    assert module.SymbolCallHierarchyService._call_references(None, limit=10) == ()


def test_call_reference_filter_honours_limit_without_consuming_tail():
    module = _load_module()
    consumed = []

    def records():
        for index in range(5):
            consumed.append(index)
            yield _Reference("call")

    result = module.SymbolCallHierarchyService._call_references(records(), limit=2)

    assert len(result) == 2
    assert consumed == [0, 1]
