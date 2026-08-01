"""AST based semantic relation extraction for the SAE code graph."""
from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SemanticNode:
    node_id: str
    kind: str
    name: str
    qualified_name: str
    path: str
    line: int
    end_line: int
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticEdge:
    source_id: str
    target: str
    kind: str
    path: str
    line: int
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticBuildResult:
    path: str
    nodes: tuple[SemanticNode, ...]
    edges: tuple[SemanticEdge, ...]
    parse_error: str | None = None


class SemanticGraphBuilder:
    MAX_SOURCE_BYTES = 16 * 1024 * 1024

    """Builds a deterministic semantic graph without executing source code."""

    def parse_file(self, path: str | Path) -> SemanticBuildResult:
        if not isinstance(path, (str, Path)):
            return SemanticBuildResult(str(path), (), (), "TypeError: Source path must be text or pathlib.Path.")
        candidate: Path | None = None
        try:
            candidate = Path(path).expanduser().resolve(strict=False)
            size = candidate.stat().st_size
            if size > self.MAX_SOURCE_BYTES:
                return SemanticBuildResult(str(candidate), (), (), f"ValueError: Source file is too large: {size} bytes (maximum {self.MAX_SOURCE_BYTES}).")
            # Follow Python's source-decoding rules (UTF-8 by default, BOM and
            # PEP 263 coding-cookie support) instead of forcing UTF-8.
            with tokenize.open(candidate) as stream:
                source = stream.read()
        except (OSError, UnicodeError, SyntaxError, TypeError, ValueError, RuntimeError) as exc:
            failed_path = str(candidate) if candidate is not None else str(path)
            return SemanticBuildResult(failed_path, (), (), f"{type(exc).__name__}: {exc}")
        return self.parse_source(source, path=candidate)

    def parse_source(self, source: str, *, path: str | Path = "<memory>") -> SemanticBuildResult:
        filename = str(path)
        if not isinstance(source, str):
            return SemanticBuildResult(filename, (), (), "TypeError: Source must be text.")
        try:
            tree = ast.parse(source, filename=filename, type_comments=True)
        except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError) as exc:
            return SemanticBuildResult(filename, (), (), f"{type(exc).__name__}: {exc}")
        visitor = _SemanticVisitor(filename)
        try:
            visitor.visit(tree)
        except (RecursionError, MemoryError) as exc:
            return SemanticBuildResult(filename, (), (), f"{type(exc).__name__}: {exc}")
        nodes = tuple(sorted(visitor.nodes, key=lambda item: (item.line, item.qualified_name, item.kind)))
        edges = tuple(sorted(visitor.edges, key=lambda item: (item.line, item.source_id, item.kind, item.target)))
        return SemanticBuildResult(filename, nodes, edges)


class _SemanticVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.nodes: list[SemanticNode] = []
        self.edges: list[SemanticEdge] = []
        self._scope: list[str] = []
        self._node_stack: list[str] = []
        self._context: list[str] = []
        self._module_id = f"file:{path}"
        self.nodes.append(SemanticNode(self._module_id, "file", Path(path).name, path, path, 1, 1))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        node_id = self._add_symbol(node.name, "class", node)
        self._contains(node_id, node)
        for base in node.bases:
            self._edge(node_id, self._expr(base), "inherits", node)
        for decorator in node.decorator_list:
            self._edge(node_id, self._expr(decorator), "decorated_by", node)
        self._scope.append(node.name)
        self._node_stack.append(node_id)
        self._context.append("class")
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._context.pop()
            self._node_stack.pop()
            self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, async_function=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, async_function=True)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, async_function: bool) -> None:
        is_method = bool(self._context and self._context[-1] == "class")
        kind = "async_method" if async_function and is_method else "method" if is_method else "async_function" if async_function else "function"
        node_id = self._add_symbol(node.name, kind, node, metadata=(("signature", self._signature(node.args)),))
        self._contains(node_id, node)
        for decorator in node.decorator_list:
            self._edge(node_id, self._expr(decorator), "decorated_by", node)
        if node.returns is not None:
            self._edge(node_id, self._expr(node.returns), "returns_type", node)
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        for argument in arguments:
            if argument.annotation is not None:
                self._edge(node_id, self._expr(argument.annotation), "parameter_type", argument, metadata=(("parameter", argument.arg),))
        self._scope.append(node.name)
        self._node_stack.append(node_id)
        self._context.append("function")
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._context.pop()
            self._node_stack.pop()
            self._scope.pop()

    def visit_Call(self, node: ast.Call) -> None:
        self._edge(self._current_source(), self._expr(node.func), "calls", node)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._edge(self._current_source(), alias.name, "imports", node, metadata=(("alias", alias.asname or ""),))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            target = f"{module}.{alias.name}" if module else alias.name
            self._edge(self._current_source(), target, "imports_symbol", node, metadata=(("alias", alias.asname or ""),))

    def visit_Assign(self, node: ast.Assign) -> None:
        if not self._node_stack:
            for target in node.targets:
                for name in self._target_names(target):
                    node_id = self._add_symbol(name, "constant" if name.isupper() else "global_variable", node)
                    self._contains(node_id, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if not self._node_stack and isinstance(node.target, ast.Name):
            name = node.target.id
            node_id = self._add_symbol(name, "constant" if name.isupper() else "global_variable", node)
            self._contains(node_id, node)
            self._edge(node_id, self._expr(node.annotation), "has_type", node)
        self.generic_visit(node)

    def _add_symbol(self, name: str, kind: str, node: ast.AST, *, metadata: tuple[tuple[str, str], ...] = ()) -> str:
        qualified = ".".join((*self._scope, name)) if self._scope else name
        node_id = f"symbol:{self.path}:{qualified}:{getattr(node, 'lineno', 1)}"
        self.nodes.append(SemanticNode(node_id, kind, name, qualified, self.path, int(getattr(node, "lineno", 1)), int(getattr(node, "end_lineno", getattr(node, "lineno", 1))), metadata))
        return node_id

    def _contains(self, target_id: str, node: ast.AST) -> None:
        self._edge(self._current_source(), target_id, "contains", node)

    def _current_source(self) -> str:
        return self._node_stack[-1] if self._node_stack else self._module_id

    def _edge(self, source_id: str, target: str, kind: str, node: ast.AST, *, metadata: tuple[tuple[str, str], ...] = ()) -> None:
        if target:
            self.edges.append(SemanticEdge(source_id, target, kind, self.path, int(getattr(node, "lineno", 1)), metadata))

    @staticmethod
    def _expr(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return getattr(node, "id", getattr(node, "attr", ""))

    @staticmethod
    def _signature(args: ast.arguments) -> str:
        names = [item.arg for item in (*args.posonlyargs, *args.args)]
        if args.vararg:
            names.append(f"*{args.vararg.arg}")
        names.extend(item.arg for item in args.kwonlyargs)
        if args.kwarg:
            names.append(f"**{args.kwarg.arg}")
        return f"({', '.join(names)})"

    @staticmethod
    def _target_names(node: ast.AST) -> tuple[str, ...]:
        if isinstance(node, ast.Name):
            return (node.id,)
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(item.id for item in node.elts if isinstance(item, ast.Name))
        return ()
