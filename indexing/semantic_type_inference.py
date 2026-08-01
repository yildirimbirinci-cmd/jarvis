"""Basic, deterministic AST type inference for Python source files."""
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
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from .semantic_core import SemanticAnalysisResult, SemanticSymbol, SemanticType
from .semantic_scope_analyzer import SemanticScopeAnalyzer


UNKNOWN = SemanticType("Unknown", confidence=0.0, source="inference")
NONE = SemanticType("None", source="literal")


class BasicTypeInferencer(ast.NodeVisitor):
    """Infer local symbol types from annotations and simple expressions.

    This stage intentionally avoids cross-file and control-flow propagation;
    those belong to later semantic-analysis packages.
    """

    def __init__(self) -> None:
        self._env_stack: list[dict[str, SemanticType]] = [{}]
        self._types: dict[tuple[int, str], SemanticType] = {}
        self._scope_depth = 0

    @classmethod
    def analyze_source(cls, path: str | Path, source: str) -> SemanticAnalysisResult:
        base = SemanticScopeAnalyzer.analyze_source(path, source)
        if base.parse_error:
            return base
        tree = ast.parse(source, filename=str(path), type_comments=True)
        visitor = cls()
        visitor.visit(tree)
        symbols = tuple(visitor._apply_type(symbol) for symbol in base.symbols)
        return SemanticAnalysisResult(
            path=base.path,
            scopes=base.scopes,
            symbols=symbols,
            diagnostics=base.diagnostics,
            parse_error=base.parse_error,
        )

    @classmethod
    def analyze_file(cls, path: str | Path, *, encoding: str = "utf-8") -> SemanticAnalysisResult:
        file_path = Path(path)
        try:
            source = file_path.read_text(encoding=encoding)
        except (OSError, UnicodeError):
            return SemanticScopeAnalyzer.analyze_file(file_path, encoding=encoding)
        return cls.analyze_source(file_path, source)

    def _apply_type(self, symbol: SemanticSymbol) -> SemanticSymbol:
        key = (symbol.location.line, symbol.name)
        inferred = self._types.get(key)
        if inferred is None:
            return symbol
        return replace(symbol, inferred_type=inferred)

    @property
    def _env(self) -> dict[str, SemanticType]:
        return self._env_stack[-1]

    def _record(self, name: str, node: ast.AST, inferred: SemanticType) -> None:
        self._env[name] = inferred
        self._types[(getattr(node, "lineno", 1), name)] = inferred

    def _bind_target(self, target: ast.AST, inferred: SemanticType) -> None:
        if isinstance(target, ast.Name):
            self._record(target.id, target, inferred)
        elif isinstance(target, (ast.Tuple, ast.List)):
            item_types = inferred.arguments if inferred.name in {"tuple", "list"} else ()
            for index, item in enumerate(target.elts):
                self._bind_target(item, item_types[index] if index < len(item_types) else UNKNOWN)

    def visit_Assign(self, node: ast.Assign) -> None:
        inferred = self.infer_expr(node.value)
        for target in node.targets:
            self._bind_target(target, inferred)
        self.generic_visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        inferred = self.from_annotation(node.annotation)
        if node.value is not None and inferred.name == "Unknown":
            inferred = self.infer_expr(node.value)
        self._bind_target(node.target, inferred)
        if node.value is not None:
            self.generic_visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        inferred = self.infer_expr(node.value)
        self._bind_target(node.target, inferred)
        self.generic_visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        iterable = self.infer_expr(node.iter)
        item = iterable.arguments[0] if iterable.arguments else UNKNOWN
        self._bind_target(node.target, item)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        function_type = SemanticType("Callable", source="definition")
        self._record(node.name, node, function_type)
        self._env_stack.append({})
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self._record(arg.arg, arg, self.from_annotation(arg.annotation) if arg.annotation else UNKNOWN)
        if node.args.vararg:
            self._record(node.args.vararg.arg, node.args.vararg, SemanticType("tuple", (UNKNOWN,), source="parameter"))
        if node.args.kwarg:
            self._record(node.args.kwarg.arg, node.args.kwarg, SemanticType("dict", (SemanticType("str"), UNKNOWN), source="parameter"))
        for statement in node.body:
            self.visit(statement)
        self._env_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node.name, node, SemanticType("type", (SemanticType(node.name),), source="definition"))
        self._env_stack.append({})
        for statement in node.body:
            self.visit(statement)
        self._env_stack.pop()

    def infer_expr(self, node: ast.AST | None) -> SemanticType:
        if node is None:
            return NONE
        if isinstance(node, ast.Constant):
            if node.value is None:
                return NONE
            return SemanticType(type(node.value).__name__, source="literal")
        if isinstance(node, ast.Name):
            for env in reversed(self._env_stack):
                if node.id in env:
                    return env[node.id]
            return UNKNOWN
        if isinstance(node, ast.List):
            return SemanticType("list", (self._merge(self.infer_expr(item) for item in node.elts),), source="literal")
        if isinstance(node, ast.Set):
            return SemanticType("set", (self._merge(self.infer_expr(item) for item in node.elts),), source="literal")
        if isinstance(node, ast.Tuple):
            return SemanticType("tuple", tuple(self.infer_expr(item) for item in node.elts), source="literal")
        if isinstance(node, ast.Dict):
            keys = [self.infer_expr(item) for item in node.keys if item is not None]
            values = [self.infer_expr(item) for item in node.values]
            return SemanticType("dict", (self._merge(keys), self._merge(values)), source="literal")
        if isinstance(node, ast.UnaryOp):
            return SemanticType("bool", source="operator") if isinstance(node.op, ast.Not) else self.infer_expr(node.operand)
        if isinstance(node, (ast.Compare, ast.BoolOp)):
            return SemanticType("bool", source="operator")
        if isinstance(node, ast.BinOp):
            left, right = self.infer_expr(node.left), self.infer_expr(node.right)
            if left.name == right.name:
                return SemanticType(left.name, left.arguments, confidence=min(left.confidence, right.confidence), source="operator")
            if {left.name, right.name} <= {"int", "float"}:
                return SemanticType("float", source="operator")
            return UNKNOWN
        if isinstance(node, ast.IfExp):
            return self._merge((self.infer_expr(node.body), self.infer_expr(node.orelse)))
        if isinstance(node, ast.JoinedStr):
            return SemanticType("str", source="literal")
        if isinstance(node, ast.Lambda):
            return SemanticType("Callable", source="definition")
        if isinstance(node, ast.Call):
            name = self._call_name(node.func)
            constructors = {"str", "int", "float", "bool", "bytes", "list", "tuple", "set", "dict"}
            if name in constructors:
                return SemanticType(name, source="constructor")
            if name in {"len", "hash", "id"}:
                return SemanticType("int", source="builtin")
            if name in {"repr", "format", "input"}:
                return SemanticType("str", source="builtin")
            if name in {"range", "enumerate", "zip", "map", "filter"}:
                return SemanticType("Iterable", (UNKNOWN,), source="builtin")
            return UNKNOWN
        if isinstance(node, ast.ListComp):
            return SemanticType("list", (self.infer_expr(node.elt),), source="comprehension")
        if isinstance(node, ast.SetComp):
            return SemanticType("set", (self.infer_expr(node.elt),), source="comprehension")
        if isinstance(node, ast.DictComp):
            return SemanticType("dict", (self.infer_expr(node.key), self.infer_expr(node.value)), source="comprehension")
        if isinstance(node, ast.GeneratorExp):
            return SemanticType("Iterator", (self.infer_expr(node.elt),), source="comprehension")
        return UNKNOWN

    @staticmethod
    def _call_name(node: ast.AST) -> str:
        return node.id if isinstance(node, ast.Name) else ""

    @classmethod
    def from_annotation(cls, node: ast.AST | None) -> SemanticType:
        if node is None:
            return UNKNOWN
        if isinstance(node, ast.Name):
            return SemanticType(node.id, source="annotation")
        if isinstance(node, ast.Constant):
            if node.value is None:
                return SemanticType("None", source="annotation")
            if isinstance(node.value, str):
                return SemanticType(node.value, source="annotation")
        if isinstance(node, ast.Attribute):
            prefix = cls._annotation_name(node.value)
            return SemanticType(f"{prefix}.{node.attr}" if prefix else node.attr, source="annotation")
        if isinstance(node, ast.Subscript):
            name = cls._annotation_name(node.value) or "Unknown"
            values = node.slice.elts if isinstance(node.slice, ast.Tuple) else (node.slice,)
            arguments = tuple(cls.from_annotation(value) for value in values)
            if name in {"Optional", "typing.Optional"} and len(arguments) == 1:
                return SemanticType(arguments[0].name, arguments[0].arguments, nullable=True, source="annotation")
            return SemanticType(name, arguments, source="annotation")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            parts = [cls.from_annotation(node.left), cls.from_annotation(node.right)]
            non_none = [item for item in parts if item.name != "None"]
            if len(non_none) == 1:
                item = non_none[0]
                return SemanticType(item.name, item.arguments, nullable=True, source="annotation")
            return SemanticType("Union", tuple(parts), source="annotation")
        return UNKNOWN

    @classmethod
    def _annotation_name(cls, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = cls._annotation_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    @staticmethod
    def _merge(values: Iterable[SemanticType]) -> SemanticType:
        items = tuple(values)
        if not items:
            return UNKNOWN
        first = items[0]
        if all(item.name == first.name and item.arguments == first.arguments for item in items):
            return SemanticType(first.name, first.arguments, nullable=any(item.nullable for item in items), confidence=min(item.confidence for item in items), source="merged")
        unique: list[SemanticType] = []
        for item in items:
            if item not in unique:
                unique.append(item)
        return SemanticType("Union", tuple(unique), confidence=min(item.confidence for item in items), source="merged")

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
