"""Control-flow aware type propagation for Python semantic symbols."""
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
from typing import Iterable, Mapping

from .semantic_core import SemanticAnalysisResult, SemanticSymbol, SemanticType
from .semantic_type_inference import BasicTypeInferencer, NONE, UNKNOWN


class TypePropagationAnalyzer:
    """Propagate local types across assignments, branches, loops and calls.

    The analyzer is intentionally deterministic and bounded. It performs a
    small fixed-point pass over each lexical scope and uses inferred function
    return types to refine direct calls in the same source file.
    """

    def __init__(self, path: str | Path, tree: ast.Module) -> None:
        self.path = str(path)
        self.tree = tree
        self.module_name = self._derive_module_name(self.path)
        self.function_returns: dict[str, SemanticType] = {}
        self.scope_envs: dict[str, dict[str, SemanticType]] = {}
        self._scope_counts: dict[str, int] = {}

    @classmethod
    def analyze_source(cls, path: str | Path, source: str) -> SemanticAnalysisResult:
        base = BasicTypeInferencer.analyze_source(path, source)
        if base.parse_error:
            return base
        tree = ast.parse(source, filename=str(path), type_comments=True)
        analyzer = cls(path, tree)
        analyzer._run()
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
            return BasicTypeInferencer.analyze_file(file_path, encoding=encoding)
        return cls.analyze_source(file_path, source)

    @staticmethod
    def _derive_module_name(path: str) -> str:
        stem = Path(path).stem.strip()
        return stem if stem and stem != "__init__" else "module"

    def _run(self) -> None:
        # Function returns can depend on earlier function calls. A bounded pass
        # keeps the result predictable while resolving common local chains.
        for _ in range(4):
            before = dict(self.function_returns)
            self._scope_counts.clear()
            module_env: dict[str, SemanticType] = {}
            self._process_block(self.tree.body, module_env, self.module_name)
            self.scope_envs[self.module_name] = module_env
            if before == self.function_returns:
                break

    def _apply(self, symbol: SemanticSymbol) -> SemanticSymbol:
        inferred = self.scope_envs.get(symbol.scope_id, {}).get(symbol.name)
        if inferred is None:
            return symbol
        return replace(symbol, inferred_type=inferred)

    def _unique_scope(self, parent: str, name: str) -> str:
        base = f"{parent}.{name}"
        count = self._scope_counts.get(base, 0) + 1
        self._scope_counts[base] = count
        return base if count == 1 else f"{base}#{count}"

    def _process_block(
        self,
        statements: Iterable[ast.stmt],
        env: dict[str, SemanticType],
        scope_id: str,
    ) -> tuple[SemanticType, ...]:
        returns: list[SemanticType] = []
        for statement in statements:
            returns.extend(self._process_statement(statement, env, scope_id))
        self.scope_envs[scope_id] = dict(env)
        return tuple(returns)

    def _process_statement(
        self,
        node: ast.stmt,
        env: dict[str, SemanticType],
        scope_id: str,
    ) -> tuple[SemanticType, ...]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            child_id = self._unique_scope(scope_id, node.name)
            local = self._parameter_env(node.args)
            returned = self._process_block(node.body, local, child_id)
            annotation = BasicTypeInferencer.from_annotation(node.returns)
            return_type = annotation if annotation.name != "Unknown" else self._merge(returned)
            self.function_returns[node.name] = return_type
            env[node.name] = SemanticType(
                "Callable", (return_type,), confidence=return_type.confidence, source="propagation"
            )
            return ()

        if isinstance(node, ast.ClassDef):
            child_id = self._unique_scope(scope_id, node.name)
            local: dict[str, SemanticType] = {}
            self._process_block(node.body, local, child_id)
            env[node.name] = SemanticType("type", (SemanticType(node.name),), source="definition")
            return ()

        if isinstance(node, ast.Assign):
            value = self._infer(node.value, env)
            for target in node.targets:
                self._bind(target, value, env)
            return ()

        if isinstance(node, ast.AnnAssign):
            annotation = BasicTypeInferencer.from_annotation(node.annotation)
            value = annotation if annotation.name != "Unknown" else self._infer(node.value, env)
            self._bind(node.target, value, env)
            return ()

        if isinstance(node, ast.AugAssign):
            current = self._infer(node.target, env)
            value = self._infer(node.value, env)
            self._bind(node.target, self._binary(current, value), env)
            return ()

        if isinstance(node, ast.If):
            left = dict(env)
            right = dict(env)
            left_returns = self._process_block(node.body, left, scope_id)
            right_returns = self._process_block(node.orelse, right, scope_id) if node.orelse else ()
            self._merge_envs(env, left, right)
            return (*left_returns, *right_returns)

        if isinstance(node, (ast.For, ast.AsyncFor)):
            item = self._iter_item(self._infer(node.iter, env))
            loop_env = dict(env)
            self._bind(node.target, item, loop_env)
            returned = self._process_block(node.body, loop_env, scope_id)
            self._merge_envs(env, env, loop_env)
            if node.orelse:
                returned += self._process_block(node.orelse, env, scope_id)
            return returned

        if isinstance(node, ast.While):
            loop_env = dict(env)
            returned = self._process_block(node.body, loop_env, scope_id)
            self._merge_envs(env, env, loop_env)
            if node.orelse:
                returned += self._process_block(node.orelse, env, scope_id)
            return returned

        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    self._bind(item.optional_vars, self._infer(item.context_expr, env), env)
            return self._process_block(node.body, env, scope_id)

        if isinstance(node, ast.Try):
            branches: list[dict[str, SemanticType]] = []
            returns: list[SemanticType] = []
            body_env = dict(env)
            returns.extend(self._process_block(node.body, body_env, scope_id))
            branches.append(body_env)
            for handler in node.handlers:
                handler_env = dict(env)
                if handler.name:
                    handler_env[handler.name] = SemanticType("Exception", source="propagation")
                returns.extend(self._process_block(handler.body, handler_env, scope_id))
                branches.append(handler_env)
            if node.orelse:
                returns.extend(self._process_block(node.orelse, body_env, scope_id))
            if branches:
                merged = branches[0]
                for branch in branches[1:]:
                    target: dict[str, SemanticType] = {}
                    self._merge_envs(target, merged, branch)
                    merged = target
                env.update(merged)
            if node.finalbody:
                returns.extend(self._process_block(node.finalbody, env, scope_id))
            return tuple(returns)

        if isinstance(node, ast.Return):
            return (self._infer(node.value, env),)

        if isinstance(node, ast.Expr) and isinstance(node.value, ast.NamedExpr):
            value = self._infer(node.value.value, env)
            self._bind(node.value.target, value, env)
        return ()

    def _parameter_env(self, args: ast.arguments) -> dict[str, SemanticType]:
        env: dict[str, SemanticType] = {}
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            env[arg.arg] = BasicTypeInferencer.from_annotation(arg.annotation) if arg.annotation else UNKNOWN
        if args.vararg:
            env[args.vararg.arg] = SemanticType("tuple", (UNKNOWN,), source="parameter")
        if args.kwarg:
            env[args.kwarg.arg] = SemanticType("dict", (SemanticType("str"), UNKNOWN), source="parameter")
        return env

    def _infer(self, node: ast.AST | None, env: Mapping[str, SemanticType]) -> SemanticType:
        if node is None:
            return NONE
        if isinstance(node, ast.Name):
            return env.get(node.id, UNKNOWN)
        if isinstance(node, ast.Constant):
            return NONE if node.value is None else SemanticType(type(node.value).__name__, source="literal")
        if isinstance(node, ast.List):
            return SemanticType("list", (self._merge(self._infer(item, env) for item in node.elts),), source="propagation")
        if isinstance(node, ast.Set):
            return SemanticType("set", (self._merge(self._infer(item, env) for item in node.elts),), source="propagation")
        if isinstance(node, ast.Tuple):
            return SemanticType("tuple", tuple(self._infer(item, env) for item in node.elts), source="propagation")
        if isinstance(node, ast.Dict):
            keys = (self._infer(item, env) for item in node.keys if item is not None)
            values = (self._infer(item, env) for item in node.values)
            return SemanticType("dict", (self._merge(keys), self._merge(values)), source="propagation")
        if isinstance(node, ast.BinOp):
            return self._binary(self._infer(node.left, env), self._infer(node.right, env))
        if isinstance(node, (ast.BoolOp, ast.Compare)):
            return SemanticType("bool", source="operator")
        if isinstance(node, ast.UnaryOp):
            return SemanticType("bool", source="operator") if isinstance(node.op, ast.Not) else self._infer(node.operand, env)
        if isinstance(node, ast.IfExp):
            return self._merge((self._infer(node.body, env), self._infer(node.orelse, env)))
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if name in self.function_returns:
                return self.function_returns[name]
            builtin = BasicTypeInferencer()
            builtin._env_stack = [dict(env)]
            return builtin.infer_expr(node)
        if isinstance(node, ast.ListComp):
            comp_env = dict(env)
            self._bind_generators(node.generators, comp_env)
            return SemanticType("list", (self._infer(node.elt, comp_env),), source="propagation")
        if isinstance(node, ast.SetComp):
            comp_env = dict(env)
            self._bind_generators(node.generators, comp_env)
            return SemanticType("set", (self._infer(node.elt, comp_env),), source="propagation")
        if isinstance(node, ast.DictComp):
            comp_env = dict(env)
            self._bind_generators(node.generators, comp_env)
            return SemanticType("dict", (self._infer(node.key, comp_env), self._infer(node.value, comp_env)), source="propagation")
        if isinstance(node, ast.GeneratorExp):
            comp_env = dict(env)
            self._bind_generators(node.generators, comp_env)
            return SemanticType("Iterator", (self._infer(node.elt, comp_env),), source="propagation")
        return UNKNOWN

    def _bind_generators(self, generators: Iterable[ast.comprehension], env: dict[str, SemanticType]) -> None:
        for generator in generators:
            self._bind(generator.target, self._iter_item(self._infer(generator.iter, env)), env)

    def _bind(self, target: ast.AST, inferred: SemanticType, env: dict[str, SemanticType]) -> None:
        if isinstance(target, ast.Name):
            env[target.id] = inferred
        elif isinstance(target, (ast.Tuple, ast.List)):
            values = inferred.arguments if inferred.name == "tuple" else ()
            for index, item in enumerate(target.elts):
                self._bind(item, values[index] if index < len(values) else self._iter_item(inferred), env)

    @staticmethod
    def _iter_item(inferred: SemanticType) -> SemanticType:
        if inferred.name == "tuple":
            return TypePropagationAnalyzer._merge(inferred.arguments)
        if inferred.name in {"list", "set", "Iterable", "Iterator"} and inferred.arguments:
            return inferred.arguments[0]
        if inferred.name == "dict" and inferred.arguments:
            return inferred.arguments[0]
        return UNKNOWN

    @staticmethod
    def _binary(left: SemanticType, right: SemanticType) -> SemanticType:
        if left.name == right.name and left.name != "Unknown":
            return SemanticType(left.name, left.arguments, confidence=min(left.confidence, right.confidence), source="propagation")
        if {left.name, right.name} <= {"int", "float"}:
            return SemanticType("float", source="propagation")
        return UNKNOWN

    @classmethod
    def _merge_envs(
        cls,
        destination: dict[str, SemanticType],
        left: Mapping[str, SemanticType],
        right: Mapping[str, SemanticType],
    ) -> None:
        for name in left.keys() | right.keys():
            values = tuple(value for value in (left.get(name), right.get(name)) if value is not None)
            destination[name] = cls._merge(values)

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
        first = unique[0]
        if len(unique) == 1:
            return SemanticType(
                first.name,
                first.arguments,
                nullable=first.nullable,
                confidence=first.confidence,
                source="propagation",
            )
        nullable = any(item.name == "None" or item.nullable for item in unique)
        non_none = [item for item in unique if item.name != "None"]
        if len(non_none) == 1 and nullable:
            item = non_none[0]
            return SemanticType(item.name, item.arguments, nullable=True, confidence=item.confidence, source="propagation")
        return SemanticType("Union", tuple(unique), confidence=min(item.confidence for item in unique), source="propagation")

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
