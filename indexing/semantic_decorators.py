"""Decorator-aware semantic analysis for Python definitions.

The analyzer records decorator order, arguments and well-known semantic effects
without importing or executing project code.  Results are immutable and safe to
feed into later graph, review and patch-planning stages.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .semantic_core import SemanticType
from .semantic_type_inference import BasicTypeInferencer


UNKNOWN = SemanticType("Unknown", confidence=0.0, source="decorator")


@dataclass(frozen=True, slots=True)
class DecoratorReference:
    """Normalized source-level decorator reference."""

    name: str
    arguments: tuple[str, ...] = ()
    keyword_arguments: tuple[tuple[str, str], ...] = ()
    line: int = 1

    @property
    def display_name(self) -> str:
        if not self.arguments and not self.keyword_arguments:
            return self.name
        values = [*self.arguments, *(f"{key}={value}" for key, value in self.keyword_arguments)]
        return f"{self.name}({', '.join(values)})"


@dataclass(frozen=True, slots=True)
class DecoratedDefinition:
    """Semantic view of a decorated function, method or class."""

    name: str
    qualified_name: str
    kind: str
    decorators: tuple[DecoratorReference, ...]
    declared_type: SemanticType
    effective_type: SemanticType
    flags: frozenset[str] = frozenset()
    line: int = 1

    def has_decorator(self, name: str) -> bool:
        short = name.rsplit(".", 1)[-1]
        return any(item.name == name or item.name.rsplit(".", 1)[-1] == short for item in self.decorators)


@dataclass(frozen=True, slots=True)
class DecoratorAnalysisResult:
    path: str
    definitions: tuple[DecoratedDefinition, ...] = ()
    parse_error: str | None = None

    def by_qualified_name(self, qualified_name: str) -> DecoratedDefinition | None:
        return next((item for item in self.definitions if item.qualified_name == qualified_name), None)


class DecoratorAnalyzer(ast.NodeVisitor):
    """Statically analyze decorator semantics and composition order."""

    _PROPERTY = {"property", "cached_property", "functools.cached_property"}
    _CLASSMETHOD = {"classmethod"}
    _STATICMETHOD = {"staticmethod"}
    _ABSTRACT = {"abstractmethod", "abc.abstractmethod", "abstractclassmethod", "abstractstaticmethod"}
    _CACHE = {"cache", "functools.cache", "lru_cache", "functools.lru_cache"}
    _DATACLASS = {"dataclass", "dataclasses.dataclass"}
    _OVERLOAD = {"overload", "typing.overload"}
    _FINAL = {"final", "typing.final"}

    def __init__(self, path: str) -> None:
        self.path = path
        self._scope: list[str] = []
        self._definitions: list[DecoratedDefinition] = []

    @classmethod
    def analyze_source(cls, path: str | Path, source: str) -> DecoratorAnalysisResult:
        normalized = str(Path(path).expanduser().resolve(strict=False))
        try:
            tree = ast.parse(source, filename=normalized, type_comments=True)
        except SyntaxError as exc:
            return DecoratorAnalysisResult(normalized, parse_error=f"{exc.msg} (line {exc.lineno or 1})")
        analyzer = cls(normalized)
        analyzer.visit(tree)
        return DecoratorAnalysisResult(normalized, tuple(analyzer._definitions))

    @classmethod
    def analyze_file(cls, path: str | Path, *, encoding: str = "utf-8") -> DecoratorAnalysisResult:
        file_path = Path(path).expanduser().resolve(strict=False)
        try:
            source = file_path.read_text(encoding=encoding)
        except (OSError, UnicodeError) as exc:
            return DecoratorAnalysisResult(str(file_path), parse_error=str(exc))
        return cls.analyze_source(file_path, source)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = self._qualified(node.name)
        decorators = self._decorators(node.decorator_list)
        flags = self._flags(decorators, kind="class")
        declared = SemanticType("type", (SemanticType(node.name, source="definition"),), source="definition")
        effective = self._effective_class_type(node, decorators, declared)
        self._definitions.append(
            DecoratedDefinition(node.name, qualified, "class", decorators, declared, effective, flags, node.lineno)
        )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool) -> None:
        qualified = self._qualified(node.name)
        decorators = self._decorators(node.decorator_list)
        kind = "method" if self._scope else "function"
        return_type = BasicTypeInferencer.from_annotation(node.returns)
        parameters = tuple(
            BasicTypeInferencer.from_annotation(arg.annotation)
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        )
        declared = SemanticType("Callable", (*parameters, return_type), source="definition")
        flags = set(self._flags(decorators, kind=kind))
        if is_async:
            flags.add("async")
        effective = self._effective_function_type(node, decorators, declared, return_type, is_async=is_async)
        self._definitions.append(
            DecoratedDefinition(node.name, qualified, kind, decorators, declared, effective, frozenset(flags), node.lineno)
        )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _qualified(self, name: str) -> str:
        return ".".join((*self._scope, name)) if self._scope else name

    @classmethod
    def _decorators(cls, nodes: Iterable[ast.expr]) -> tuple[DecoratorReference, ...]:
        return tuple(cls._decorator(node) for node in nodes)

    @classmethod
    def _decorator(cls, node: ast.expr) -> DecoratorReference:
        if isinstance(node, ast.Call):
            return DecoratorReference(
                cls._name(node.func),
                tuple(cls._source(argument) for argument in node.args),
                tuple((keyword.arg or "**", cls._source(keyword.value)) for keyword in node.keywords),
                getattr(node, "lineno", 1),
            )
        return DecoratorReference(cls._name(node), line=getattr(node, "lineno", 1))

    @classmethod
    def _name(cls, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = cls._name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        if isinstance(node, ast.Subscript):
            return cls._name(node.value)
        return cls._source(node)

    @staticmethod
    def _source(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except (AttributeError, ValueError):
            return node.__class__.__name__

    @classmethod
    def _matches(cls, decorators: Iterable[DecoratorReference], names: set[str]) -> bool:
        short_names = {name.rsplit(".", 1)[-1] for name in names}
        return any(item.name in names or item.name.rsplit(".", 1)[-1] in short_names for item in decorators)

    @classmethod
    def _flags(cls, decorators: tuple[DecoratorReference, ...], *, kind: str) -> frozenset[str]:
        flags: set[str] = set()
        if cls._matches(decorators, cls._PROPERTY):
            flags.add("property")
        if cls._matches(decorators, cls._CLASSMETHOD):
            flags.add("classmethod")
        if cls._matches(decorators, cls._STATICMETHOD):
            flags.add("staticmethod")
        if cls._matches(decorators, cls._ABSTRACT):
            flags.add("abstract")
        if cls._matches(decorators, cls._CACHE):
            flags.add("cached")
        if cls._matches(decorators, cls._DATACLASS):
            flags.add("dataclass")
        if cls._matches(decorators, cls._OVERLOAD):
            flags.add("overload")
        if cls._matches(decorators, cls._FINAL):
            flags.add("final")
        if kind == "method" and any(item.name.endswith(".setter") for item in decorators):
            flags.add("property_setter")
        if kind == "method" and any(item.name.endswith(".deleter") for item in decorators):
            flags.add("property_deleter")
        return frozenset(flags)

    @classmethod
    def _effective_function_type(
        cls,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        decorators: tuple[DecoratorReference, ...],
        declared: SemanticType,
        return_type: SemanticType,
        *,
        is_async: bool,
    ) -> SemanticType:
        result = declared
        # Python applies decorators bottom-to-top; process in that order.
        for decorator in reversed(decorators):
            short = decorator.name.rsplit(".", 1)[-1]
            if decorator.name in cls._PROPERTY or short in {name.rsplit(".", 1)[-1] for name in cls._PROPERTY}:
                result = SemanticType("property", (return_type,), source="decorator")
            elif short == "setter":
                result = SemanticType("property", (return_type,), source="decorator")
            elif short == "deleter":
                result = SemanticType("property", (return_type,), source="decorator")
            elif decorator.name in cls._CLASSMETHOD or short == "classmethod":
                result = SemanticType("classmethod", (result,), source="decorator")
            elif decorator.name in cls._STATICMETHOD or short == "staticmethod":
                result = SemanticType("staticmethod", (result,), source="decorator")
            elif decorator.name in cls._OVERLOAD or short == "overload":
                result = SemanticType("overload", (result,), source="decorator")
            elif decorator.name in cls._CACHE or short in {"cache", "lru_cache"}:
                result = SemanticType("cached", (result,), source="decorator")
            elif decorator.name in cls._ABSTRACT or short.startswith("abstract"):
                result = SemanticType("abstract", (result,), source="decorator")
            elif decorator.name in cls._FINAL or short == "final":
                result = SemanticType("final", (result,), source="decorator")
            else:
                result = SemanticType("Decorated", (result,), confidence=0.7, source=decorator.name or "decorator")
        if is_async and result.name == "Callable" and return_type.name != "Unknown":
            async_return = SemanticType("Coroutine", (SemanticType("Any"), SemanticType("Any"), return_type), source="async")
            result = SemanticType("Callable", (*declared.arguments[:-1], async_return), source="definition")
        return result

    @classmethod
    def _effective_class_type(
        cls,
        node: ast.ClassDef,
        decorators: tuple[DecoratorReference, ...],
        declared: SemanticType,
    ) -> SemanticType:
        result = declared
        for decorator in reversed(decorators):
            short = decorator.name.rsplit(".", 1)[-1]
            if decorator.name in cls._DATACLASS or short == "dataclass":
                result = SemanticType("dataclass", (SemanticType(node.name, source="definition"),), source="decorator")
            elif decorator.name in cls._FINAL or short == "final":
                result = SemanticType("final", (result,), source="decorator")
            else:
                result = SemanticType("DecoratedClass", (result,), confidence=0.7, source=decorator.name or "decorator")
        return result

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
