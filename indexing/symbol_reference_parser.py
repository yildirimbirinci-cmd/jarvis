"""AST based symbol-reference extraction for incremental SAE indexing."""
from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SymbolReferenceRecord:
    name: str
    path: str
    line: int
    column: int
    context: str
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class SymbolReferenceParseResult:
    path: str
    references: tuple[SymbolReferenceRecord, ...]
    parse_error: str | None = None


class SymbolReferenceParser:
    MAX_SOURCE_BYTES = 16 * 1024 * 1024

    """Extract symbol reads/calls without importing or executing source files."""

    def parse_file(self, path: str | Path) -> SymbolReferenceParseResult:
        if not isinstance(path, (str, Path)):
            return SymbolReferenceParseResult(str(path), (), "TypeError: Source path must be text or pathlib.Path.")
        candidate: Path | None = None
        try:
            candidate = Path(path).expanduser().resolve(strict=False)
            size = candidate.stat().st_size
            if size > self.MAX_SOURCE_BYTES:
                return SymbolReferenceParseResult(str(candidate), (), f"ValueError: Source file is too large: {size} bytes (maximum {self.MAX_SOURCE_BYTES}).")
            # Follow Python's source-decoding rules (UTF-8 by default, BOM and
            # PEP 263 coding-cookie support) instead of forcing UTF-8.
            with tokenize.open(candidate) as stream:
                source = stream.read()
        except (OSError, UnicodeError, SyntaxError, TypeError, ValueError, RuntimeError) as exc:
            failed_path = str(candidate) if candidate is not None else str(path)
            return SymbolReferenceParseResult(failed_path, (), f"{type(exc).__name__}: {exc}")
        return self.parse_source(source, path=candidate)

    def parse_source(self, source: str, *, path: str | Path = "<memory>") -> SymbolReferenceParseResult:
        filename = str(path)
        try:
            tree = ast.parse(source, filename=filename, type_comments=True)
        except SyntaxError as exc:
            return SymbolReferenceParseResult(filename, (), f"SyntaxError: {exc}")
        visitor = _ReferenceVisitor(filename)
        try:
            visitor.visit(tree)
        except (RecursionError, MemoryError) as exc:
            return SymbolReferenceParseResult(filename, (), f"{type(exc).__name__}: {exc}")
        ordered = tuple(sorted(visitor.references, key=lambda item: (item.line, item.column, item.name)))
        return SymbolReferenceParseResult(filename, ordered)


class _ReferenceVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.references: list[SymbolReferenceRecord] = []
        self._scope: list[str] = []
        self._parents: list[ast.AST] = []

    def visit(self, node: ast.AST):  # type: ignore[override]
        self._parents.append(node)
        try:
            return super().visit(node)
        finally:
            self._parents.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        self._scope.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        arguments = (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        )
        if node.args.vararg is not None:
            arguments += (node.args.vararg,)
        if node.args.kwarg is not None:
            arguments += (node.args.kwarg,)
        for argument in arguments:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        for default in (*node.args.defaults, *(item for item in node.args.kw_defaults if item is not None)):
            self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        self._scope.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self._scope.pop()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._append(node.id, node, self._context_for(node))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self._append(_attribute_name(node), node, self._context_for(node))
        self.visit(node.value)

    def _append(self, name: str, node: ast.AST, context: str) -> None:
        self.references.append(
            SymbolReferenceRecord(
                name=name,
                path=self.path,
                line=int(getattr(node, "lineno", 1)),
                column=int(getattr(node, "col_offset", 0)),
                context=context,
                scope=".".join(self._scope) or None,
            )
        )

    def _context_for(self, node: ast.AST) -> str:
        parent = self._parents[-2] if len(self._parents) > 1 else None
        if isinstance(parent, ast.Call) and parent.func is node:
            return "call"
        if isinstance(parent, ast.ImportFrom):
            return "import"
        if isinstance(parent, ast.Subscript):
            return "subscript"
        return "read"


def _attribute_name(node: ast.Attribute) -> str:
    """Return the longest static dotted name available for an attribute read."""
    parts = [node.attr]
    value: ast.AST = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))
