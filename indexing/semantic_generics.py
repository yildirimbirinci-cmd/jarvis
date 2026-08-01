"""Generic type analysis and TypeVar substitution for Python source files."""
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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping

from .semantic_core import SemanticAnalysisResult, SemanticSymbol, SemanticType
from .semantic_type_inference import BasicTypeInferencer, UNKNOWN
from .semantic_type_propagation import TypePropagationAnalyzer


@dataclass(frozen=True, slots=True)
class TypeVariable:
    """A normalized TypeVar declaration."""

    name: str
    constraints: tuple[SemanticType, ...] = ()
    bound: SemanticType | None = None


@dataclass(frozen=True, slots=True)
class GenericFunctionSignature:
    """Generic function parameters and return annotation."""

    name: str
    parameters: tuple[tuple[str, SemanticType], ...]
    return_type: SemanticType
    type_parameters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GenericClassSignature:
    """Generic class parameters and constructor field annotations."""

    name: str
    type_parameters: tuple[str, ...]
    constructor_parameters: tuple[tuple[str, SemanticType], ...] = ()


class GenericTypeAnalyzer:
    """Resolve local TypeVars, generic functions and generic constructors.

    This stage builds on control-flow propagation. It is deliberately local to
    one source file and deterministic; cross-file generic resolution belongs to
    a later semantic graph integration stage.
    """

    def __init__(self, path: str | Path, tree: ast.Module) -> None:
        self.path = str(path)
        self.tree = tree
        self.module_name = self._derive_module_name(self.path)
        self.type_variables: dict[str, TypeVariable] = {}
        self.functions: dict[str, GenericFunctionSignature] = {}
        self.classes: dict[str, GenericClassSignature] = {}
        self.overrides: dict[tuple[str, str], SemanticType] = {}
        self._scope_stack: list[str] = [self.module_name]
        self._env_stack: list[dict[str, SemanticType]] = [{}]

    @classmethod
    def analyze_source(cls, path: str | Path, source: str) -> SemanticAnalysisResult:
        base = TypePropagationAnalyzer.analyze_source(path, source)
        if base.parse_error:
            return base
        tree = ast.parse(source, filename=str(path), type_comments=True)
        analyzer = cls(path, tree)
        analyzer._collect_declarations()
        analyzer._analyze_block(tree.body)
        symbols = tuple(analyzer._apply(symbol) for symbol in base.symbols)
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
            return TypePropagationAnalyzer.analyze_file(file_path, encoding=encoding)
        return cls.analyze_source(file_path, source)

    @staticmethod
    def _derive_module_name(path: str) -> str:
        stem = Path(path).stem.strip()
        return stem if stem and stem != "__init__" else "module"

    @property
    def _scope_id(self) -> str:
        return self._scope_stack[-1]

    @property
    def _env(self) -> dict[str, SemanticType]:
        return self._env_stack[-1]

    def _apply(self, symbol: SemanticSymbol) -> SemanticSymbol:
        inferred = self.overrides.get((symbol.scope_id, symbol.name))
        return replace(symbol, inferred_type=inferred) if inferred is not None else symbol

    def _record(self, name: str, inferred: SemanticType) -> None:
        self._env[name] = inferred
        self.overrides[(self._scope_id, name)] = inferred

    def _collect_declarations(self) -> None:
        for node in self.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                self._collect_typevar(node)
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                signature = self._function_signature(node)
                if signature.type_parameters:
                    self.functions[node.name] = signature
            elif isinstance(node, ast.ClassDef):
                signature = self._class_signature(node)
                if signature.type_parameters:
                    self.classes[node.name] = signature

    def _collect_typevar(self, node: ast.Assign | ast.AnnAssign) -> None:
        value = node.value
        targets: tuple[ast.AST, ...]
        if isinstance(node, ast.Assign):
            targets = tuple(node.targets)
        else:
            targets = (node.target,)
        if not isinstance(value, ast.Call) or self._name(value.func) not in {"TypeVar", "typing.TypeVar"}:
            return
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not names:
            return
        declared = names[0]
        if value.args and isinstance(value.args[0], ast.Constant) and isinstance(value.args[0].value, str):
            declared = value.args[0].value
        constraints = tuple(self._annotation(arg) for arg in value.args[1:])
        bound = None
        for keyword in value.keywords:
            if keyword.arg == "bound":
                bound = self._annotation(keyword.value)
        variable = TypeVariable(declared, constraints, bound)
        for name in names:
            self.type_variables[name] = variable

    def _function_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> GenericFunctionSignature:
        parameters: list[tuple[str, SemanticType]] = []
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            parameters.append((arg.arg, self._annotation(arg.annotation)))
        if node.args.vararg:
            parameters.append((node.args.vararg.arg, SemanticType("tuple", (self._annotation(node.args.vararg.annotation),), source="generic")))
        if node.args.kwarg:
            parameters.append((node.args.kwarg.arg, SemanticType("dict", (SemanticType("str"), self._annotation(node.args.kwarg.annotation)), source="generic")))
        return_type = self._annotation(node.returns)
        referenced = self._referenced_typevars(tuple(value for _, value in parameters) + (return_type,))
        return GenericFunctionSignature(node.name, tuple(parameters), return_type, referenced)

    def _class_signature(self, node: ast.ClassDef) -> GenericClassSignature:
        type_parameters: list[str] = []
        for base in node.bases:
            if isinstance(base, ast.Subscript) and self._name(base.value) in {"Generic", "typing.Generic"}:
                values = base.slice.elts if isinstance(base.slice, ast.Tuple) else (base.slice,)
                for value in values:
                    name = self._name(value)
                    if name in self.type_variables and name not in type_parameters:
                        type_parameters.append(name)
        constructor: list[tuple[str, SemanticType]] = []
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__":
                args = (*child.args.posonlyargs, *child.args.args)
                for arg in args:
                    if arg.arg != "self":
                        constructor.append((arg.arg, self._annotation(arg.annotation)))
                break
        return GenericClassSignature(node.name, tuple(type_parameters), tuple(constructor))

    def _analyze_block(self, statements: Iterable[ast.stmt]) -> None:
        for node in statements:
            self._analyze_statement(node)

    def _analyze_statement(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Assign):
            inferred = self._infer(node.value)
            for target in node.targets:
                self._bind(target, inferred)
            return
        if isinstance(node, ast.AnnAssign):
            annotated = self._annotation(node.annotation)
            inferred = self._infer(node.value) if node.value is not None else annotated
            if inferred.name == "Unknown":
                inferred = annotated
            self._bind(node.target, inferred)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._analyze_function(node)
            return
        if isinstance(node, ast.ClassDef):
            class_type = SemanticType("type", (SemanticType(node.name, tuple(SemanticType(item, source="typevar") for item in self.classes.get(node.name, GenericClassSignature(node.name, ())).type_parameters), source="generic"),), source="definition")
            self._record(node.name, class_type)
            return
        if isinstance(node, ast.If):
            self._analyze_block(node.body)
            self._analyze_block(node.orelse)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self._bind(node.target, self._iter_item(self._infer(node.iter)))
            self._analyze_block(node.body)
            self._analyze_block(node.orelse)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars:
                    self._bind(item.optional_vars, self._infer(item.context_expr))
            self._analyze_block(node.body)
            return
        if isinstance(node, ast.Try):
            self._analyze_block(node.body)
            for handler in node.handlers:
                self._analyze_block(handler.body)
            self._analyze_block(node.orelse)
            self._analyze_block(node.finalbody)

    def _analyze_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent_scope = self._scope_id
        scope_id = f"{parent_scope}.{node.name}"
        self._scope_stack.append(scope_id)
        env: dict[str, SemanticType] = {}
        self._env_stack.append(env)
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            inferred = self._annotation(arg.annotation)
            self._record(arg.arg, inferred)
        self._analyze_block(node.body)
        self._env_stack.pop()
        self._scope_stack.pop()

    def _infer(self, node: ast.AST | None) -> SemanticType:
        if node is None:
            return SemanticType("None", source="literal")
        if isinstance(node, ast.Name):
            for env in reversed(self._env_stack):
                if node.id in env:
                    return env[node.id]
            if node.id in self.type_variables:
                return SemanticType(node.id, source="typevar")
            return UNKNOWN
        if isinstance(node, ast.Constant):
            return SemanticType("None", source="literal") if node.value is None else SemanticType(type(node.value).__name__, source="literal")
        if isinstance(node, ast.List):
            return SemanticType("list", (self._merge(self._infer(item) for item in node.elts),), source="generic")
        if isinstance(node, ast.Set):
            return SemanticType("set", (self._merge(self._infer(item) for item in node.elts),), source="generic")
        if isinstance(node, ast.Tuple):
            return SemanticType("tuple", tuple(self._infer(item) for item in node.elts), source="generic")
        if isinstance(node, ast.Dict):
            keys = (self._infer(item) for item in node.keys if item is not None)
            values = (self._infer(item) for item in node.values)
            return SemanticType("dict", (self._merge(keys), self._merge(values)), source="generic")
        if isinstance(node, ast.Subscript):
            return self._annotation(node)
        if isinstance(node, ast.Call):
            return self._infer_call(node)
        if isinstance(node, ast.IfExp):
            return self._merge((self._infer(node.body), self._infer(node.orelse)))
        if isinstance(node, ast.ListComp):
            return SemanticType("list", (self._infer(node.elt),), source="generic")
        fallback = BasicTypeInferencer()
        fallback._env_stack = [dict(env) for env in self._env_stack]
        return fallback.infer_expr(node)

    def _infer_call(self, node: ast.Call) -> SemanticType:
        name = self._name(node.func)
        simple_name = name.rsplit(".", 1)[-1]
        if simple_name in self.functions:
            signature = self.functions[simple_name]
            mapping: dict[str, SemanticType] = {}
            positional = [self._infer(arg) for arg in node.args]
            for (_, declared), actual in zip(signature.parameters, positional):
                self._unify(declared, actual, mapping)
            for keyword in node.keywords:
                if keyword.arg is None:
                    continue
                for parameter, declared in signature.parameters:
                    if parameter == keyword.arg:
                        self._unify(declared, self._infer(keyword.value), mapping)
                        break
            return self._substitute(signature.return_type, mapping)
        if simple_name in self.classes:
            signature = self.classes[simple_name]
            mapping: dict[str, SemanticType] = {}
            positional = [self._infer(arg) for arg in node.args]
            for (_, declared), actual in zip(signature.constructor_parameters, positional):
                self._unify(declared, actual, mapping)
            arguments = tuple(mapping.get(parameter, SemanticType(parameter, source="typevar")) for parameter in signature.type_parameters)
            return SemanticType(simple_name, arguments, source="generic-constructor")
        fallback = BasicTypeInferencer()
        fallback._env_stack = [dict(env) for env in self._env_stack]
        return fallback.infer_expr(node)

    def _annotation(self, node: ast.AST | None) -> SemanticType:
        inferred = BasicTypeInferencer.from_annotation(node)
        return self._normalize_annotation(inferred)

    def _normalize_annotation(self, inferred: SemanticType) -> SemanticType:
        aliases = {
            "typing.List": "list", "List": "list",
            "typing.Dict": "dict", "Dict": "dict",
            "typing.Set": "set", "Set": "set",
            "typing.Tuple": "tuple", "Tuple": "tuple",
            "typing.Sequence": "Sequence", "typing.Iterable": "Iterable",
            "typing.Iterator": "Iterator", "typing.Callable": "Callable",
            "typing.Union": "Union", "Union": "Union",
        }
        name = aliases.get(inferred.name, inferred.name)
        arguments = tuple(self._normalize_annotation(item) for item in inferred.arguments)
        return SemanticType(name, arguments, nullable=inferred.nullable, confidence=inferred.confidence, source=inferred.source)

    def _unify(self, declared: SemanticType, actual: SemanticType, mapping: dict[str, SemanticType]) -> None:
        if declared.name in self.type_variables:
            current = mapping.get(declared.name)
            mapping[declared.name] = actual if current is None else self._merge((current, actual))
            return
        if declared.name == actual.name and declared.arguments and actual.arguments:
            for expected, received in zip(declared.arguments, actual.arguments):
                self._unify(expected, received, mapping)

    def _substitute(self, inferred: SemanticType, mapping: Mapping[str, SemanticType]) -> SemanticType:
        if inferred.name in mapping:
            replacement = mapping[inferred.name]
            return SemanticType(
                replacement.name,
                replacement.arguments,
                nullable=inferred.nullable or replacement.nullable,
                confidence=replacement.confidence,
                source="generic-substitution",
            )
        return SemanticType(
            inferred.name,
            tuple(self._substitute(item, mapping) for item in inferred.arguments),
            nullable=inferred.nullable,
            confidence=inferred.confidence,
            source="generic-substitution" if inferred.arguments else inferred.source,
        )

    def _referenced_typevars(self, values: Iterable[SemanticType]) -> tuple[str, ...]:
        found: list[str] = []
        def collect(item: SemanticType) -> None:
            if item.name in self.type_variables and item.name not in found:
                found.append(item.name)
            for argument in item.arguments:
                collect(argument)
        for value in values:
            collect(value)
        return tuple(found)

    def _bind(self, target: ast.AST, inferred: SemanticType) -> None:
        if isinstance(target, ast.Name):
            self._record(target.id, inferred)
        elif isinstance(target, (ast.Tuple, ast.List)):
            values = inferred.arguments if inferred.name == "tuple" else ()
            for index, item in enumerate(target.elts):
                self._bind(item, values[index] if index < len(values) else self._iter_item(inferred))

    @staticmethod
    def _iter_item(inferred: SemanticType) -> SemanticType:
        if inferred.name == "tuple":
            return GenericTypeAnalyzer._merge(inferred.arguments)
        if inferred.name in {"list", "set", "Sequence", "Iterable", "Iterator"} and inferred.arguments:
            return inferred.arguments[0]
        if inferred.name == "dict" and inferred.arguments:
            return inferred.arguments[0]
        return UNKNOWN

    @staticmethod
    def _name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = GenericTypeAnalyzer._name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    @staticmethod
    def _merge(values: Iterable[SemanticType]) -> SemanticType:
        items = tuple(values)
        if not items:
            return UNKNOWN
        unique: list[SemanticType] = []
        for item in items:
            if item.name == "Unknown" and len(items) > 1:
                continue
            if item not in unique:
                unique.append(item)
        if not unique:
            return UNKNOWN
        if len(unique) == 1:
            return unique[0]
        return SemanticType("Union", tuple(unique), confidence=min(item.confidence for item in unique), source="generic-merge")

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
