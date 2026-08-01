from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from indexing.semantic_core import (
    DiagnosticSeverity,
    ScopeKind,
    SemanticAnalysisResult,
    SemanticDiagnostic,
    SemanticScope,
    SemanticSymbol,
    SemanticType,
    SourceLocation,
    SymbolKind,
)


def location(line: int = 1) -> SourceLocation:
    return SourceLocation("project/module.py", line, 0, line, 5)


def test_semantic_type_formats_nested_generic_and_nullable() -> None:
    value = SemanticType(
        "dict",
        (SemanticType("str"), SemanticType("list", (SemanticType("int"),))),
        nullable=True,
        confidence=0.75,
        source="annotation",
    )
    assert value.display_name == "dict[str, list[int]] | None"


def test_models_are_immutable_and_metadata_is_read_only() -> None:
    symbol = SemanticSymbol(
        "value",
        "module.value",
        SymbolKind.VARIABLE,
        location(),
        "module",
        metadata={"origin": "assignment"},
    )
    with pytest.raises(FrozenInstanceError):
        symbol.name = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        symbol.metadata["origin"] = "changed"  # type: ignore[index]


def test_result_validates_scope_graph_and_supports_queries() -> None:
    module_scope = SemanticScope("module", "module", ScopeKind.MODULE, location())
    function_scope = SemanticScope("module.run", "run", ScopeKind.FUNCTION, location(2), "module")
    symbol = SemanticSymbol(
        "item",
        "module.run.item",
        SymbolKind.PARAMETER,
        location(2),
        function_scope.id,
        SemanticType("str", source="annotation"),
    )
    result = SemanticAnalysisResult("project/module.py", (module_scope, function_scope), (symbol,))
    assert result.symbols_in_scope("module.run") == (symbol,)
    assert result.has_errors is False


def test_result_rejects_unknown_parent_and_symbol_scope() -> None:
    with pytest.raises(ValueError, match="unknown parent scope"):
        SemanticAnalysisResult(
            "module.py",
            (SemanticScope("child", "child", ScopeKind.FUNCTION, location(), "missing"),),
        )
    with pytest.raises(ValueError, match="unknown symbol scope"):
        SemanticAnalysisResult(
            "module.py",
            (SemanticScope("module", "module", ScopeKind.MODULE, location()),),
            (SemanticSymbol("x", "module.x", SymbolKind.VARIABLE, location(), "missing"),),
        )


def test_diagnostic_error_state_and_validation() -> None:
    diagnostic = SemanticDiagnostic(
        "SEM001",
        "Unresolved symbol",
        DiagnosticSeverity.ERROR,
        location(),
    )
    result = SemanticAnalysisResult(
        "module.py",
        (SemanticScope("module", "module", ScopeKind.MODULE, location()),),
        diagnostics=(diagnostic,),
    )
    assert result.has_errors is True
    with pytest.raises(ValueError, match="confidence"):
        SemanticType("int", confidence=float("nan"))
    with pytest.raises(ValueError, match="end before"):
        SourceLocation("module.py", 2, 0, 1, 0)


def test_scope_symbol_references_are_deduplicated_in_order() -> None:
    scope = SemanticScope(
        "module",
        "module",
        ScopeKind.MODULE,
        location(),
        symbols=("module.a", "module.a", "module.b"),
    )
    assert scope.symbols == ("module.a", "module.b")
