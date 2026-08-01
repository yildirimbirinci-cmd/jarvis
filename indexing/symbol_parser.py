"""AST based Python symbol extraction for incremental SAE indexing."""
from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class SymbolRecord:
    name: str
    qualified_name: str
    kind: str
    path: str
    line: int = 1
    end_line: int = 1
    column: int = 0
    parent: str | None = None
    decorators: tuple[str, ...] = ()
    bases: tuple[str, ...] = ()
    signature: str = ""


@dataclass(frozen=True, slots=True)
class SymbolParseResult:
    path: str
    symbols: tuple[SymbolRecord, ...]
    parse_error: str | None = None


class SymbolParser:
    MAX_SOURCE_BYTES = 16 * 1024 * 1024

    """Extracts navigable symbols without importing or executing source code."""

    def parse_file(self, path: str | Path) -> SymbolParseResult:
        if not isinstance(path, (str, Path)):
            return SymbolParseResult(str(path), (), "TypeError: Source path must be text or pathlib.Path.")
        candidate: Path | None = None
        try:
            candidate = Path(path).expanduser().resolve(strict=False)
            size = candidate.stat().st_size
            if size > self.MAX_SOURCE_BYTES:
                return SymbolParseResult(str(candidate), (), f"ValueError: Source file is too large: {size} bytes (maximum {self.MAX_SOURCE_BYTES}).")
            # Follow Python's source-decoding rules (UTF-8 by default, BOM and
            # PEP 263 coding-cookie support) instead of forcing UTF-8.
            with tokenize.open(candidate) as stream:
                source = stream.read()
        except (OSError, UnicodeError, SyntaxError, TypeError, ValueError, RuntimeError) as exc:
            failed_path = str(candidate) if candidate is not None else str(path)
            return SymbolParseResult(failed_path, (), f"{type(exc).__name__}: {exc}")
        return self.parse_source(source, path=candidate)

    def parse_source(self, source: str, *, path: str | Path = "<memory>") -> SymbolParseResult:
        filename = str(path)
        try:
            tree = ast.parse(source, filename=filename, type_comments=True)
        except (SyntaxError, RecursionError, MemoryError) as exc:
            return SymbolParseResult(filename, (), f"{type(exc).__name__}: {exc}")

        visitor = _SymbolVisitor(filename)
        try:
            visitor.visit(tree)
        except (RecursionError, MemoryError) as exc:
            return SymbolParseResult(filename, (), f"{type(exc).__name__}: {exc}")
        ordered = tuple(sorted(visitor.symbols, key=lambda item: (item.line, item.column, item.qualified_name)))
        return SymbolParseResult(filename, ordered)


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.symbols: list[SymbolRecord] = []
        self._scope: list[str] = []
        self._class_depth = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = self._qualify(node.name)
        self.symbols.append(
            self._record(
                node,
                node.name,
                qualified,
                "enum" if self._is_enum(node.bases) else "dataclass" if self._is_dataclass(node) else "class",
                decorators=self._decorators(node.decorator_list),
                bases=tuple(self._expr_text(base) for base in node.bases),
            )
        )
        self._scope.append(node.name)
        self._class_depth += 1
        self.generic_visit(node)
        self._class_depth -= 1
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._class_depth == 0 and not self._scope:
            for name in self._assignment_names(node.targets):
                self.symbols.append(self._record(node, name, name, self._variable_kind(name)))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._class_depth == 0 and not self._scope and isinstance(node.target, ast.Name):
            name = node.target.id
            self.symbols.append(self._record(node, name, name, self._variable_kind(name)))
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool) -> None:
        qualified = self._qualify(node.name)
        if self._class_depth:
            kind = "async_method" if is_async else "method"
        else:
            kind = "async_function" if is_async else "function"
        self.symbols.append(
            self._record(
                node,
                node.name,
                qualified,
                kind,
                decorators=self._decorators(node.decorator_list),
                signature=self._signature(node.args),
            )
        )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _record(
        self,
        node: ast.AST,
        name: str,
        qualified_name: str,
        kind: str,
        *,
        decorators: Iterable[str] = (),
        bases: Iterable[str] = (),
        signature: str = "",
    ) -> SymbolRecord:
        parent = ".".join(self._scope) or None
        return SymbolRecord(
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            path=self.path,
            line=int(getattr(node, "lineno", 1)),
            end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
            column=int(getattr(node, "col_offset", 0)),
            parent=parent,
            decorators=tuple(decorators),
            bases=tuple(bases),
            signature=signature,
        )

    def _qualify(self, name: str) -> str:
        return ".".join((*self._scope, name)) if self._scope else name

    @staticmethod
    def _variable_kind(name: str) -> str:
        return "constant" if name.isupper() else "global_variable"

    @staticmethod
    def _assignment_names(targets: Iterable[ast.expr]) -> tuple[str, ...]:
        names: list[str] = []
        for target in targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, (ast.Tuple, ast.List)):
                names.extend(item.id for item in target.elts if isinstance(item, ast.Name))
        return tuple(names)

    @staticmethod
    def _decorators(nodes: Iterable[ast.expr]) -> tuple[str, ...]:
        return tuple(_SymbolVisitor._expr_text(node) for node in nodes)

    @staticmethod
    def _is_dataclass(node: ast.ClassDef) -> bool:
        return any(_SymbolVisitor._expr_text(item).split(".")[-1] == "dataclass" for item in node.decorator_list)

    @staticmethod
    def _is_enum(bases: Iterable[ast.expr]) -> bool:
        return any(_SymbolVisitor._expr_text(base).split(".")[-1] in {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"} for base in bases)

    @staticmethod
    def _signature(args: ast.arguments) -> str:
        parts: list[str] = []
        positional = [*args.posonlyargs, *args.args]
        defaults_offset = len(positional) - len(args.defaults)
        for index, arg in enumerate(positional):
            text = arg.arg
            if arg.annotation is not None:
                text += f": {_SymbolVisitor._expr_text(arg.annotation)}"
            if index >= defaults_offset:
                text += " = ..."
            parts.append(text)
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        elif args.kwonlyargs:
            parts.append("*")
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            text = arg.arg
            if arg.annotation is not None:
                text += f": {_SymbolVisitor._expr_text(arg.annotation)}"
            if default is not None:
                text += " = ..."
            parts.append(text)
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")
        return f"({', '.join(parts)})"

    @staticmethod
    def _expr_text(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                return node.attr
            return ""
