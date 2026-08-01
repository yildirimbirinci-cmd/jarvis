"""AST-based lexical scope analysis for Python source files."""
from __future__ import annotations

import sys


def _register_import_alias() -> None:
    """Keep legacy and package-qualified imports bound to one module object."""
    if __name__.startswith("artmach_assistant.indexing."):
        alias = __name__.removeprefix("artmach_assistant.")
    elif __name__.startswith("indexing."):
        alias = f"artmach_assistant.{__name__}"
    else:
        return
    sys.modules.setdefault(alias, sys.modules[__name__])


_register_import_alias()

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .semantic_core import (
    DiagnosticSeverity,
    ScopeKind,
    SemanticAnalysisResult,
    SemanticDiagnostic,
    SemanticScope,
    SemanticSymbol,
    SourceLocation,
    SymbolKind,
)


@dataclass(slots=True)
class _ScopeState:
    id: str
    name: str
    kind: ScopeKind
    location: SourceLocation
    parent: "_ScopeState | None" = None
    symbols: list[str] = field(default_factory=list)
    local_names: set[str] = field(default_factory=set)
    globals: set[str] = field(default_factory=set)
    nonlocals: set[str] = field(default_factory=set)


class SemanticScopeAnalyzer(ast.NodeVisitor):
    """Build deterministic lexical scopes and symbol declarations from Python AST."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(Path(path))
        self._module_name = self._derive_module_name(self.path)
        self._scope_stack: list[_ScopeState] = []
        self._scopes: list[_ScopeState] = []
        self._symbols: list[SemanticSymbol] = []
        self._diagnostics: list[SemanticDiagnostic] = []
        self._scope_counters: dict[str, int] = {}

    @classmethod
    def analyze_source(cls, path: str | Path, source: str) -> SemanticAnalysisResult:
        if not isinstance(source, str):
            raise TypeError("source must be a string")
        analyzer = cls(path)
        try:
            tree = ast.parse(source, filename=analyzer.path, type_comments=True)
        except SyntaxError as exc:
            location = SourceLocation(
                analyzer.path,
                max(1, exc.lineno or 1),
                max(0, (exc.offset or 1) - 1),
            )
            diagnostic = SemanticDiagnostic(
                "SEM_SCOPE_PARSE_ERROR",
                exc.msg or "invalid syntax",
                DiagnosticSeverity.ERROR,
                location,
            )
            return SemanticAnalysisResult(
                analyzer.path,
                diagnostics=(diagnostic,),
                parse_error=exc.msg or "invalid syntax",
            )

        module_location = analyzer._location(tree)
        module = _ScopeState(
            analyzer._module_name,
            analyzer._module_name,
            ScopeKind.MODULE,
            module_location,
        )
        analyzer._push_scope(module)
        analyzer.visit(tree)
        analyzer._pop_scope()
        return analyzer._result()

    @classmethod
    def analyze_file(cls, path: str | Path, *, encoding: str = "utf-8") -> SemanticAnalysisResult:
        file_path = Path(path)
        try:
            source = file_path.read_text(encoding=encoding)
        except (OSError, UnicodeError) as exc:
            location = SourceLocation(str(file_path), 1)
            diagnostic = SemanticDiagnostic(
                "SEM_SCOPE_READ_ERROR",
                str(exc),
                DiagnosticSeverity.ERROR,
                location,
            )
            return SemanticAnalysisResult(
                str(file_path),
                diagnostics=(diagnostic,),
                parse_error=str(exc),
            )
        return cls.analyze_source(file_path, source)

    @staticmethod
    def _derive_module_name(path: str) -> str:
        stem = Path(path).stem.strip()
        return stem if stem and stem != "__init__" else "module"

    @property
    def _current(self) -> _ScopeState:
        return self._scope_stack[-1]

    def _push_scope(self, state: _ScopeState) -> None:
        self._scope_stack.append(state)
        self._scopes.append(state)

    def _pop_scope(self) -> _ScopeState:
        return self._scope_stack.pop()

    def _location(self, node: ast.AST) -> SourceLocation:
        line = max(1, int(getattr(node, "lineno", 1) or 1))
        column = max(0, int(getattr(node, "col_offset", 0) or 0))
        end_line = max(line, int(getattr(node, "end_lineno", line) or line))
        end_column = max(0, int(getattr(node, "end_col_offset", column) or column))
        if end_line == line:
            end_column = max(column, end_column)
        return SourceLocation(self.path, line, column, end_line, end_column)

    def _unique_scope_id(self, base: str) -> str:
        count = self._scope_counters.get(base, 0) + 1
        self._scope_counters[base] = count
        return base if count == 1 else f"{base}#{count}"

    def _child_scope(self, name: str, kind: ScopeKind, node: ast.AST) -> _ScopeState:
        parent = self._current
        base = f"{parent.id}.{name}"
        return _ScopeState(
            self._unique_scope_id(base),
            name,
            kind,
            self._location(node),
            parent,
        )

    def _qualified_name(self, scope: _ScopeState, name: str) -> str:
        return f"{scope.id}.{name}"

    def _find_outer_binding(self, name: str) -> str | None:
        for scope in reversed(self._scope_stack[:-1]):
            if name in scope.local_names:
                return scope.id
        return None

    def _target_scope_for_name(self, name: str) -> _ScopeState:
        current = self._current
        if name in current.globals:
            return self._scope_stack[0]
        if name in current.nonlocals:
            for scope in reversed(self._scope_stack[:-1]):
                if name in scope.local_names and scope.kind is not ScopeKind.MODULE:
                    return scope
        return current

    def _declare(
        self,
        name: str,
        kind: SymbolKind,
        node: ast.AST,
        *,
        scope: _ScopeState | None = None,
        metadata: dict[str, object] | None = None,
    ) -> SemanticSymbol | None:
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()
        target = scope or self._target_scope_for_name(name)
        qualified = self._qualified_name(target, name)
        if qualified in target.symbols:
            return None

        outer_scope = self._find_outer_binding(name)
        data = dict(metadata or {})
        if outer_scope is not None and target is self._current and name not in target.nonlocals:
            data["shadows_scope"] = outer_scope
        if name in self._current.globals:
            data["declared_global"] = True
        if name in self._current.nonlocals:
            data["declared_nonlocal"] = True

        symbol = SemanticSymbol(
            name,
            qualified,
            kind,
            self._location(node),
            target.id,
            metadata=data,
        )
        target.symbols.append(qualified)
        target.local_names.add(name)
        self._symbols.append(symbol)
        return symbol

    def _declare_target(self, target: ast.AST, node: ast.AST | None = None) -> None:
        origin = node or target
        if isinstance(target, ast.Name):
            self._declare(target.id, SymbolKind.VARIABLE, origin, metadata={"origin": "assignment"})
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._declare_target(item, origin)
        elif isinstance(target, ast.Starred):
            self._declare_target(target.value, origin)
        elif isinstance(target, ast.Attribute):
            self.visit(target.value)
        elif isinstance(target, ast.Subscript):
            self.visit(target.value)
            self.visit(target.slice)

    def _declare_arguments(self, args: ast.arguments) -> None:
        ordered = [*args.posonlyargs, *args.args]
        if args.vararg is not None:
            ordered.append(args.vararg)
        ordered.extend(args.kwonlyargs)
        if args.kwarg is not None:
            ordered.append(args.kwarg)
        for argument in ordered:
            self._declare(
                argument.arg,
                SymbolKind.PARAMETER,
                argument,
                metadata={"origin": "parameter"},
            )

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = SymbolKind.METHOD if self._current.kind is ScopeKind.CLASS else SymbolKind.FUNCTION
        self._declare(node.name, kind, node, metadata={"async": isinstance(node, ast.AsyncFunctionDef)})
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

        child = self._child_scope(node.name, ScopeKind.FUNCTION, node)
        self._push_scope(child)
        self._declare_arguments(node.args)
        for statement in node.body:
            self.visit(statement)
        self._pop_scope()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._declare(node.name, SymbolKind.CLASS, node)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for decorator in node.decorator_list:
            self.visit(decorator)

        child = self._child_scope(node.name, ScopeKind.CLASS, node)
        self._push_scope(child)
        for statement in node.body:
            self.visit(statement)
        self._pop_scope()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        child = self._child_scope("<lambda>", ScopeKind.LAMBDA, node)
        self._push_scope(child)
        self._declare_arguments(node.args)
        self.visit(node.body)
        self._pop_scope()

    def _visit_comprehension_scope(self, node: ast.AST, parts: Iterable[ast.comprehension], body: Iterable[ast.AST]) -> None:
        child = self._child_scope("<comprehension>", ScopeKind.COMPREHENSION, node)
        self._push_scope(child)
        for generator in parts:
            self.visit(generator.iter)
            self._declare_target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for item in body:
            self.visit(item)
        self._pop_scope()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension_scope(node, node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension_scope(node, node.generators, (node.elt,))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension_scope(node, node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension_scope(node, node.generators, (node.key, node.value))

    def visit_Global(self, node: ast.Global) -> None:
        current = self._current
        for name in node.names:
            if name in current.nonlocals:
                self._diagnostics.append(SemanticDiagnostic(
                    "SEM_SCOPE_CONFLICTING_DECLARATION",
                    f"{name!r} is declared as both global and nonlocal",
                    DiagnosticSeverity.ERROR,
                    self._location(node),
                ))
            current.globals.add(name)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        current = self._current
        for name in node.names:
            if name in current.globals:
                self._diagnostics.append(SemanticDiagnostic(
                    "SEM_SCOPE_CONFLICTING_DECLARATION",
                    f"{name!r} is declared as both global and nonlocal",
                    DiagnosticSeverity.ERROR,
                    self._location(node),
                ))
            if current.kind is ScopeKind.MODULE or self._find_outer_binding(name) is None:
                self._diagnostics.append(SemanticDiagnostic(
                    "SEM_SCOPE_INVALID_NONLOCAL",
                    f"no binding for nonlocal {name!r} found",
                    DiagnosticSeverity.ERROR,
                    self._location(node),
                ))
            current.nonlocals.add(name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".", 1)[0]
            self._declare(name, SymbolKind.IMPORT, node, metadata={"module": alias.name})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            name = alias.asname or alias.name
            self._declare(name, SymbolKind.IMPORT, node, metadata={"module": module, "imported": alias.name})

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._declare_target(target, node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self._declare_target(node.target, node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._declare_target(node.target, node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._declare_target(node.target, node)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._declare_target(node.target, node)
        for item in [*node.body, *node.orelse]:
            self.visit(item)

    visit_AsyncFor = visit_For

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._declare_target(item.optional_vars, node)
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._declare(node.name, SymbolKind.VARIABLE, node, metadata={"origin": "exception"})
        for statement in node.body:
            self.visit(statement)

    def _result(self) -> SemanticAnalysisResult:
        scopes = tuple(
            SemanticScope(
                state.id,
                state.name,
                state.kind,
                state.location,
                state.parent.id if state.parent else None,
                tuple(state.symbols),
            )
            for state in self._scopes
        )
        return SemanticAnalysisResult(
            self.path,
            scopes,
            tuple(self._symbols),
            tuple(self._diagnostics),
        )

# Compatibility: the project historically supports both top-level and fully
# qualified imports. Bind the alternate name to this exact module object so
# dataclasses and enums retain one identity across the process.
def _register_import_alias() -> None:
    import sys

    if __name__.startswith("indexing."):
        alias = "artmach_assistant." + __name__
    elif __name__.startswith("artmach_assistant.indexing."):
        alias = __name__.removeprefix("artmach_assistant.")
    else:
        return
    sys.modules.setdefault(alias, sys.modules[__name__])


_register_import_alias()

# Compatibility: the project historically supports both top-level and fully
# qualified imports. Bind the alternate name to this exact module object so
# dataclasses and enums retain one identity across the process.
def _register_import_alias() -> None:
    import sys

    if __name__.startswith("indexing."):
        alias = "artmach_assistant." + __name__
    elif __name__.startswith("artmach_assistant.indexing."):
        alias = __name__.removeprefix("artmach_assistant.")
    else:
        return
    sys.modules.setdefault(alias, sys.modules[__name__])


_register_import_alias()
