from __future__ import annotations

from indexing.semantic_core import DiagnosticSeverity, ScopeKind, SymbolKind
from indexing.semantic_scope_analyzer import SemanticScopeAnalyzer


def _symbols(result):
    return {item.qualified_name: item for item in result.symbols}


def test_builds_module_class_function_and_nested_scopes() -> None:
    result = SemanticScopeAnalyzer.analyze_source(
        "project/example.py",
        """
import os as operating_system
value = 1
class Service:
    class_value = 2
    def run(self, item):
        local = item
        return local

def outer(arg):
    nested_value = arg
    def inner():
        return nested_value
    return inner
""",
    )
    assert result.has_errors is False
    assert [scope.kind for scope in result.scopes] == [
        ScopeKind.MODULE,
        ScopeKind.CLASS,
        ScopeKind.FUNCTION,
        ScopeKind.FUNCTION,
        ScopeKind.FUNCTION,
    ]
    symbols = _symbols(result)
    assert symbols["example.operating_system"].kind is SymbolKind.IMPORT
    assert symbols["example.Service"].kind is SymbolKind.CLASS
    assert symbols["example.Service.run"].kind is SymbolKind.METHOD
    assert symbols["example.Service.run.self"].kind is SymbolKind.PARAMETER
    assert symbols["example.outer.inner"].kind is SymbolKind.FUNCTION


def test_global_and_nonlocal_assignments_bind_to_declared_scope() -> None:
    result = SemanticScopeAnalyzer.analyze_source(
        "module.py",
        """
state = 0
def outer():
    counter = 0
    def inner():
        global state
        nonlocal counter
        state = 1
        counter = 2
""",
    )
    symbols = _symbols(result)
    assert symbols["module.state"].metadata["origin"] == "assignment"
    assert symbols["module.outer.counter"].metadata["origin"] == "assignment"
    assert "module.outer.inner.state" not in symbols
    assert "module.outer.inner.counter" not in symbols


def test_records_shadowing_without_treating_nonlocal_as_shadow() -> None:
    result = SemanticScopeAnalyzer.analyze_source(
        "module.py",
        """
value = 1
def outer():
    value = 2
    def inner():
        nonlocal value
        value = 3
""",
    )
    symbols = _symbols(result)
    assert symbols["module.outer.value"].metadata["shadows_scope"] == "module"
    assert "module.outer.inner.value" not in symbols


def test_lambda_and_comprehension_names_do_not_leak() -> None:
    result = SemanticScopeAnalyzer.analyze_source(
        "module.py",
        """
factory = lambda item: item
values = [item for item in range(3)]
""",
    )
    assert [scope.kind for scope in result.scopes] == [
        ScopeKind.MODULE,
        ScopeKind.LAMBDA,
        ScopeKind.COMPREHENSION,
    ]
    symbols = _symbols(result)
    assert any(symbol.name == "item" and symbol.scope_id.endswith("<lambda>") for symbol in result.symbols)
    assert any(symbol.name == "item" and "<comprehension>" in symbol.scope_id for symbol in result.symbols)
    assert "module.item" not in symbols


def test_reports_parse_and_invalid_nonlocal_errors() -> None:
    parse_result = SemanticScopeAnalyzer.analyze_source("broken.py", "def broken(:\n")
    assert parse_result.has_errors is True
    assert parse_result.parse_error
    assert parse_result.diagnostics[0].code == "SEM_SCOPE_PARSE_ERROR"

    invalid = SemanticScopeAnalyzer.analyze_source(
        "module.py",
        """
def outer():
    def inner():
        nonlocal missing
""",
    )
    assert invalid.has_errors is True
    assert any(
        item.code == "SEM_SCOPE_INVALID_NONLOCAL" and item.severity is DiagnosticSeverity.ERROR
        for item in invalid.diagnostics
    )


def test_duplicate_scope_names_receive_stable_unique_ids() -> None:
    result = SemanticScopeAnalyzer.analyze_source(
        "module.py",
        """
def first():
    fn = lambda: 1
    other = lambda: 2
""",
    )
    lambda_ids = [scope.id for scope in result.scopes if scope.kind is ScopeKind.LAMBDA]
    assert lambda_ids == ["module.first.<lambda>", "module.first.<lambda>#2"]
