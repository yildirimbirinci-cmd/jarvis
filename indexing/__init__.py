"""Incremental source indexing utilities for Artmach Assistant.

Public names are loaded lazily so importing :mod:`indexing` does not eagerly
import every semantic and graph subsystem. This keeps partial deployments and
tests with temporary dependency stubs isolated and prevents unrelated optional
modules from breaking package import.
"""
from __future__ import annotations

from importlib import import_module
import sys
from typing import Final



def _alias_package() -> None:
    """Keep legacy and qualified package names bound to one module object."""
    if __name__ == "indexing":
        sys.modules.setdefault("artmach_assistant.indexing", sys.modules[__name__])
    elif __name__ == "artmach_assistant.indexing":
        sys.modules.setdefault("indexing", sys.modules[__name__])


def _alias_submodule(module_name: str, module: object) -> None:
    if module_name.startswith("indexing."):
        alias = "artmach_assistant." + module_name
    elif module_name.startswith("artmach_assistant.indexing."):
        alias = module_name.removeprefix("artmach_assistant.")
    else:
        return
    sys.modules.setdefault(alias, module)


_alias_package()



def _alias_package() -> None:
    """Keep legacy and qualified package names bound to one module object."""
    if __name__ == "indexing":
        sys.modules.setdefault("artmach_assistant.indexing", sys.modules[__name__])
    elif __name__ == "artmach_assistant.indexing":
        sys.modules.setdefault("indexing", sys.modules[__name__])


def _alias_submodule(module_name: str, module: object) -> None:
    if module_name.startswith("indexing."):
        alias = "artmach_assistant." + module_name
    elif module_name.startswith("artmach_assistant.indexing."):
        alias = module_name.removeprefix("artmach_assistant.")
    else:
        return
    sys.modules.setdefault(alias, module)


_alias_package()

_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "DependencyGraph": ("dependency_graph", "DependencyGraph"),
    "DependencyGraphStats": ("dependency_graph", "DependencyGraphStats"),
    "SymbolGraphUpdatePlan": ("symbol_graph_update_planner", "SymbolGraphUpdatePlan"),
    "SymbolGraphUpdatePlanner": ("symbol_graph_update_planner", "SymbolGraphUpdatePlanner"),
    "CrossFileSymbolResolution": ("cross_file_symbol_resolver", "CrossFileSymbolResolution"),
    "CrossFileSymbolResolver": ("cross_file_symbol_resolver", "CrossFileSymbolResolver"),
    "CrossFileReferenceResolver": ("cross_file_reference_resolver", "CrossFileReferenceResolver"),
    "ReferenceBindingResult": ("cross_file_reference_resolver", "ReferenceBindingResult"),
    "ResolvedSymbolReference": ("cross_file_reference_resolver", "ResolvedSymbolReference"),
    "ProjectSymbolIndex": ("project_symbol_index", "ProjectSymbolIndex"),
    "ProjectSymbol": ("project_symbol_registry", "ProjectSymbol"),
    "ProjectSymbolRegistry": ("project_symbol_registry", "ProjectSymbolRegistry"),
    "ProjectSymbolResolution": ("project_symbol_resolver", "ProjectSymbolResolution"),
    "ProjectSymbolResolver": ("project_symbol_resolver", "ProjectSymbolResolver"),
    "DependencyResolver": ("dependency_resolver", "DependencyResolver"),
    "DependencyScanResult": ("dependency_resolver", "DependencyScanResult"),
    "SymbolDatabase": ("symbol_database", "SymbolDatabase"),
    "SymbolIndex": ("symbol_index", "SymbolIndex"),
    "SymbolParseResult": ("symbol_parser", "SymbolParseResult"),
    "SymbolParser": ("symbol_parser", "SymbolParser"),
    "SymbolRecord": ("symbol_parser", "SymbolRecord"),
    "SymbolReferenceDatabase": ("symbol_reference_database", "SymbolReferenceDatabase"),
    "SymbolReferenceIndex": ("symbol_reference_index", "SymbolReferenceIndex"),
    "SymbolReferenceParseResult": ("symbol_reference_parser", "SymbolReferenceParseResult"),
    "SymbolReferenceParser": ("symbol_reference_parser", "SymbolReferenceParser"),
    "SymbolReferenceRecord": ("symbol_reference_parser", "SymbolReferenceRecord"),
    "ResolvedType": ("type_resolver", "ResolvedType"),
    "TypeIndex": ("type_resolver", "TypeIndex"),
    "TypeResolutionResult": ("type_resolver", "TypeResolutionResult"),
    "TypeResolver": ("type_resolver", "TypeResolver"),
    "SemanticScopeAnalyzer": ("semantic_scope_analyzer", "SemanticScopeAnalyzer"),
    "BasicTypeInferencer": ("semantic_type_inference", "BasicTypeInferencer"),
    "TypePropagationAnalyzer": ("semantic_type_propagation", "TypePropagationAnalyzer"),
    "GenericClassSignature": ("semantic_generics", "GenericClassSignature"),
    "GenericFunctionSignature": ("semantic_generics", "GenericFunctionSignature"),
    "GenericTypeAnalyzer": ("semantic_generics", "GenericTypeAnalyzer"),
    "TypeVariable": ("semantic_generics", "TypeVariable"),
    "DecoratedDefinition": ("semantic_decorators", "DecoratedDefinition"),
    "DecoratorAnalysisResult": ("semantic_decorators", "DecoratorAnalysisResult"),
    "DecoratorAnalyzer": ("semantic_decorators", "DecoratorAnalyzer"),
    "DecoratorReference": ("semantic_decorators", "DecoratorReference"),
    "DiagnosticSeverity": ("semantic_core", "DiagnosticSeverity"),
    "ScopeKind": ("semantic_core", "ScopeKind"),
    "SemanticAnalysisResult": ("semantic_core", "SemanticAnalysisResult"),
    "SemanticDiagnostic": ("semantic_core", "SemanticDiagnostic"),
    "SemanticScope": ("semantic_core", "SemanticScope"),
    "SemanticSymbol": ("semantic_core", "SemanticSymbol"),
    "SemanticType": ("semantic_core", "SemanticType"),
    "SourceLocation": ("semantic_core", "SourceLocation"),
    "SymbolKind": ("semantic_core", "SymbolKind"),
    "CallGraph": ("call_graph", "CallGraph"),
    "CallGraphBuilder": ("call_graph", "CallGraphBuilder"),
    "CallGraphBuildResult": ("call_graph", "CallGraphBuildResult"),
    "CallGraphEdge": ("call_graph", "CallGraphEdge"),
    "CallGraphDiagnosticsReport": ("call_graph", "CallGraphDiagnosticsReport"),
    "CallGraphFileDiagnostics": ("call_graph", "CallGraphFileDiagnostics"),
    "CallGraphHotspot": ("call_graph", "CallGraphHotspot"),
    "CallGraphHotspotReport": ("call_graph", "CallGraphHotspotReport"),
    "CallGraphPath": ("call_graph", "CallGraphPath"),
    "CallGraphRecursionComponent": ("call_graph", "CallGraphRecursionComponent"),
    "CallGraphRecursionReport": ("call_graph", "CallGraphRecursionReport"),
    "CallGraphReachabilityReport": ("call_graph", "CallGraphReachabilityReport"),
    "CallGraphTraversalResult": ("call_graph", "CallGraphTraversalResult"),
    "CallSite": ("call_graph", "CallSite"),
    "CallSiteParser": ("call_graph", "CallSiteParser"),
    "CallTargetResolver": ("call_graph", "CallTargetResolver"),
    "GlobalSymbolEdge": ("global_symbol_graph", "GlobalSymbolEdge"),
    "GlobalSymbolGraph": ("global_symbol_graph", "GlobalSymbolGraph"),
    "GlobalSymbolGraphValidation": ("global_symbol_graph", "GlobalSymbolGraphValidation"),
    "SemanticGraph": ("semantic_graph", "SemanticGraph"),
    "SemanticBuildResult": ("semantic_graph_builder", "SemanticBuildResult"),
    "SemanticEdge": ("semantic_graph_builder", "SemanticEdge"),
    "SemanticGraphBuilder": ("semantic_graph_builder", "SemanticGraphBuilder"),
    "SemanticNode": ("semantic_graph_builder", "SemanticNode"),
    "SemanticGraphDatabase": ("semantic_graph_database", "SemanticGraphDatabase"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        module_names = {module_name for module_name, _ in _EXPORTS.values()}
        if name in module_names:
            module = import_module(f".{name}", __name__)
            _alias_submodule(module.__name__, module)
            globals()[name] = module
            return module
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    module = import_module(f".{module_name}", __name__)
    _alias_submodule(module.__name__, module)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
