"""AST-only Python call-site extraction."""
from __future__ import annotations

import ast
import tokenize
from pathlib import Path

from .model import CallSite


class CallSiteParser:
    MAX_SOURCE_BYTES = 16 * 1024 * 1024

    def parse_file(self, path: str | Path) -> tuple[tuple[CallSite, ...], str | None]:
        if not isinstance(path, (str, Path)):
            return (), "TypeError: Source path must be text or pathlib.Path."
        try:
            candidate = Path(path).expanduser().resolve(strict=False)
            size = candidate.stat().st_size
            if size > self.MAX_SOURCE_BYTES:
                return (), (
                    f"ValueError: Source file is too large: {size} bytes "
                    f"(maximum {self.MAX_SOURCE_BYTES})."
                )
            # ``tokenize.open`` follows Python's own source-decoding rules:
            # UTF-8 by default, UTF-8 BOM support, and PEP 263 coding cookies.
            # Forcing UTF-8 here incorrectly rejects otherwise valid modules.
            with tokenize.open(candidate) as stream:
                source = stream.read()
        except (OSError, UnicodeError, SyntaxError, TypeError, ValueError, RuntimeError, MemoryError) as exc:
            return (), f"{type(exc).__name__}: {exc}"
        try:
            tree = ast.parse(source, filename=str(candidate), type_comments=True)
        except (SyntaxError, RecursionError, ValueError, TypeError, MemoryError) as exc:
            return (), f"{type(exc).__name__}: {exc}"
        visitor = _CallVisitor(str(candidate))
        try:
            visitor.visit(tree)
        except (RecursionError, RuntimeError, ValueError, TypeError, MemoryError) as exc:
            return (), f"{type(exc).__name__}: {exc}"
        return tuple(sorted(visitor.calls, key=lambda item: (item.line, item.column, item.expression))), None


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.calls: list[CallSite] = []
        self._scope: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Class decorators, bases and keyword expressions execute in the
        # enclosing scope. Only the class body belongs to the class scope.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        # PEP 695/696 class type-parameter bounds and defaults are evaluated
        # in the enclosing scope, just like class bases and decorators. They
        # must be visited before entering the class body or calls such as
        # ``class Box[T: factory()]`` are either missed or attributed to Box.
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)

        self._scope.append(node.name)
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        expression = _expr_text(node.func)
        if expression:
            caller = ".".join(self._scope) or None
            self.calls.append(
                CallSite(
                    path=self.path,
                    line=int(getattr(node, "lineno", 1)),
                    column=int(getattr(node, "col_offset", 0)),
                    expression=expression,
                    caller_qualified_name=caller,
                    scope=caller,
                )
            )
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators, annotations and default values are evaluated before the
        # function exists, so they must remain attached to the enclosing caller.
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)

        self._scope.append(node.name)
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._scope.pop()

    def _visit_arguments(self, arguments: ast.arguments) -> None:
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if arguments.vararg is not None and arguments.vararg.annotation is not None:
            self.visit(arguments.vararg.annotation)
        if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
            self.visit(arguments.kwarg.annotation)
        for default in arguments.defaults:
            self.visit(default)
        for default in arguments.kw_defaults:
            if default is not None:
                self.visit(default)


def _expr_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node).strip()
    except Exception:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = _expr_text(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""
