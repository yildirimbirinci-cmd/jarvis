from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    qualified_name: str
    path: str
    line: int = 1
    column: int = 0
    end_line: int = 1
    kind: str = "function"


@dataclass(frozen=True)
class SymbolReferenceRecord:
    name: str = "target"
    path: str = "caller.py"
    line: int = 1
    column: int = 0
    context: str = "call"
    scope: str | None = None


@dataclass(frozen=True)
class CallGraphEdge:
    caller_path: str = "caller.py"
    call_line: int = 1
    call_column: int = 0
    callee_canonical_name: str = "pkg.target"
    callee_path: str = "pkg.py"


class ExactAwareIndex:
    """Simulates a crowded short-name index and an exact qualified lookup."""

    def __init__(self, exact: SymbolRecord) -> None:
        self.exact = exact
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int):
        self.queries.append(query)
        if query == "pkg.target":
            return (self.exact,)
        return tuple(
            SymbolRecord("target", f"Noise{i}.target", f"noise_{i}.py")
            for i in range(min(limit, 1000))
        )

    def symbols_for_file(self, path: str):
        return ()


class EmptyReferenceIndex:
    def references_to(self, name: str, *, limit: int):
        return ()


def install_stubs() -> None:
    package = types.ModuleType("artmach_assistant")
    package.__path__ = []
    core = types.ModuleType("artmach_assistant.core")
    core.__path__ = []
    indexing = types.ModuleType("artmach_assistant.indexing")
    validation = types.ModuleType("artmach_assistant.core.query_validation")

    validation.normalized_query = lambda value: str(value).strip() if value is not None else ""
    validation.bounded_positive_int = lambda value, default, maximum: max(
        1, min(int(value), maximum)
    )

    indexing.SymbolIndex = object
    indexing.SymbolRecord = SymbolRecord
    indexing.SymbolReferenceIndex = object
    indexing.SymbolReferenceRecord = SymbolReferenceRecord
    indexing.CrossFileReferenceResolver = object
    indexing.CallGraph = object
    indexing.CallGraphEdge = CallGraphEdge

    sys.modules.update(
        {
            "artmach_assistant": package,
            "artmach_assistant.core": core,
            "artmach_assistant.indexing": indexing,
            "artmach_assistant.core.query_validation": validation,
        }
    )


def load_module(name: str):
    tracked = (
        "artmach_assistant",
        "artmach_assistant.core",
        "artmach_assistant.indexing",
        "artmach_assistant.core.query_validation",
        f"testmods.{name}",
    )
    previous = {module_name: sys.modules.get(module_name) for module_name in tracked}
    try:
        install_stubs()
        path = Path(__file__).parents[1] / "core" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"testmods.{name}", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for module_name, old_module in previous.items():
            if old_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = old_module


def exact_record() -> SymbolRecord:
    return SymbolRecord("target", "target", "pkg.py")


def test_navigation_uses_qualified_lookup_before_filtering() -> None:
    module = load_module("symbol_navigation_service")
    index = ExactAwareIndex(exact_record())
    service = module.SymbolNavigationService(".", index, EmptyReferenceIndex())

    result = service.locate("pkg.target", limit=10)

    assert result.definitions == (exact_record(),)
    assert index.queries == ["pkg.target", "target"]


def test_hierarchy_uses_qualified_lookup_before_filtering() -> None:
    module = load_module("symbol_call_hierarchy_service")
    index = ExactAwareIndex(exact_record())
    service = module.SymbolCallHierarchyService(".", index, EmptyReferenceIndex())

    result = service.hierarchy("pkg.target", limit=10)

    assert result.definitions == (exact_record(),)
    assert index.queries == ["pkg.target", "target"]


def test_impact_analysis_uses_qualified_lookup_before_filtering() -> None:
    module = load_module("symbol_impact_analysis_service")
    index = ExactAwareIndex(exact_record())
    service = module.SymbolImpactAnalysisService(".", index, EmptyReferenceIndex())

    result = service.analyze("pkg.target", limit=10)

    assert result.definitions == (exact_record(),)
    assert index.queries == ["pkg.target", "target"]
