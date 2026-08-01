"""Static, incremental type resolution for Python workspaces."""
from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ResolvedType:
    """One statically resolved type observation."""

    symbol: str
    qualified_symbol: str
    type_name: str
    kind: str
    path: str
    line: int
    confidence: float
    source: str


@dataclass(frozen=True, slots=True)
class TypeResolutionResult:
    path: str
    records: tuple[ResolvedType, ...]
    parse_error: str | None = None


class TypeResolver:
    """Resolves explicit and safely inferable types without importing code."""

    MAX_SOURCE_BYTES = 8 * 1024 * 1024

    def parse_file(self, path: str | Path) -> TypeResolutionResult:
        try:
            candidate = Path(path).expanduser().resolve(strict=False)
        except (TypeError, ValueError, OSError) as exc:
            return TypeResolutionResult(str(path), (), f"{type(exc).__name__}: {exc}")
        try:
            size = candidate.stat().st_size
            if size > self.MAX_SOURCE_BYTES:
                return TypeResolutionResult(
                    str(candidate),
                    (),
                    f"ValueError: source file too large ({size} bytes)",
                )
            with tokenize.open(candidate) as handle:
                source = handle.read()
        except (OSError, UnicodeError, SyntaxError) as exc:
            return TypeResolutionResult(str(candidate), (), f"{type(exc).__name__}: {exc}")
        return self.parse_source(source, path=candidate)

    def parse_source(self, source: str, *, path: str | Path = "<memory>") -> TypeResolutionResult:
        filename = str(path)
        try:
            tree = ast.parse(source, filename=filename, type_comments=True)
        except (SyntaxError, RecursionError, MemoryError) as exc:
            return TypeResolutionResult(filename, (), f"{type(exc).__name__}: {exc}")
        visitor = _TypeVisitor(filename)
        visitor.visit(tree)
        records = tuple(
            sorted(
                visitor.records,
                key=lambda item: (
                    item.line,
                    item.qualified_symbol.casefold(),
                    item.kind,
                    item.type_name.casefold(),
                ),
            )
        )
        return TypeResolutionResult(filename, records)


class TypeIndex:
    """Thread-safe workspace type index with per-file incremental updates."""

    def __init__(self, project_root: str | Path, *, suffixes: Iterable[str] = (".py", ".pyi")) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        if not self.root.is_dir():
            raise NotADirectoryError(str(self.root))
        if isinstance(suffixes, str):
            suffix_values = (suffixes,)
        else:
            suffix_values = tuple(suffixes)
        normalized_suffixes = {
            value if value.startswith(".") else f".{value}"
            for item in suffix_values
            if (value := str(item).strip().casefold())
        }
        if not normalized_suffixes:
            raise ValueError("suffixes must contain at least one non-empty suffix")
        self._suffixes = frozenset(normalized_suffixes)
        self._resolver = TypeResolver()
        self._records_by_file: dict[str, tuple[ResolvedType, ...]] = {}
        self._lock = RLock()
        self._revision = 0

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def rebuild(self, paths: Iterable[str | Path] | str | Path) -> tuple[TypeResolutionResult, ...]:
        path_values = (paths,) if isinstance(paths, (str, Path)) else paths
        try:
            candidates = {self._resolve_path(path) for path in path_values}
        except (TypeError, ValueError, OSError, RuntimeError, MemoryError, RecursionError) as exc:
            raise ValueError("paths iterable failed") from exc

        staged: dict[str, tuple[ResolvedType, ...]] = {}
        results: list[TypeResolutionResult] = []
        for candidate in sorted(candidates, key=lambda item: str(item).casefold()):
            if candidate.suffix.casefold() not in self._suffixes:
                continue
            try:
                candidate.relative_to(self.root)
            except ValueError:
                continue
            if not candidate.is_file():
                results.append(TypeResolutionResult(str(candidate), ()))
                continue
            result = self._resolver.parse_file(candidate)
            results.append(result)
            if result.parse_error is not None:
                return tuple(sorted(results, key=lambda item: item.path.casefold()))
            staged[self._path_key(candidate)] = result.records

        with self._lock:
            if staged != self._records_by_file:
                self._records_by_file = staged
                self._revision += 1
        return tuple(sorted(results, key=lambda item: item.path.casefold()))

    def _resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve(strict=False)

    @staticmethod
    def _path_key(path: Path) -> str:
        return str(path).casefold()

    def update_file(self, path: str | Path) -> TypeResolutionResult | None:
        candidate = self._resolve_path(path)
        if candidate.suffix.casefold() not in self._suffixes:
            return None
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        key = self._path_key(candidate)
        with self._lock:
            if not candidate.is_file():
                if self._records_by_file.pop(key, None) is not None:
                    self._revision += 1
                return TypeResolutionResult(str(candidate), ())
            result = self._resolver.parse_file(candidate)
            if result.parse_error is None and self._records_by_file.get(key) != result.records:
                self._records_by_file[key] = result.records
                self._revision += 1
            return result

    def remove_file(self, path: str | Path) -> bool:
        candidate = self._resolve_path(path)
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return False
        key = self._path_key(candidate)
        with self._lock:
            changed = self._records_by_file.pop(key, None) is not None
            if changed:
                self._revision += 1
            return changed

    def clear(self) -> bool:
        with self._lock:
            if not self._records_by_file:
                return False
            self._records_by_file.clear()
            self._revision += 1
            return True

    def resolve(self, symbol: str, *, limit: int = 100) -> tuple[ResolvedType, ...]:
        query = symbol.strip().casefold()
        if not query:
            return ()
        with self._lock:
            records = tuple(item for values in self._records_by_file.values() for item in values)
        matches = [
            item
            for item in records
            if item.symbol.casefold() == query or item.qualified_symbol.casefold() == query
        ]
        matches.sort(key=lambda item: (-item.confidence, item.path.casefold(), item.line, item.kind))
        try:
            bounded = int(limit)
        except (TypeError, ValueError, OverflowError):
            bounded = 100
        bounded = max(1, min(bounded, 10_000))
        return tuple(matches[:bounded])

    def types_in_file(self, path: str | Path) -> tuple[ResolvedType, ...]:
        key = self._path_key(self._resolve_path(path))
        with self._lock:
            return self._records_by_file.get(key, ())

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "revision": self._revision,
                "records_by_file": {
                    path: [
                        {
                            "symbol": item.symbol,
                            "qualified_symbol": item.qualified_symbol,
                            "type_name": item.type_name,
                            "kind": item.kind,
                            "path": item.path,
                            "line": item.line,
                            "confidence": item.confidence,
                            "source": item.source,
                        }
                        for item in records
                    ]
                    for path, records in sorted(self._records_by_file.items())
                },
            }

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "resolved_types": sum(len(items) for items in self._records_by_file.values()),
                "type_files": len(self._records_by_file),
                "type_revision": self._revision,
            }


class _TypeVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.records: list[ResolvedType] = []
        self._scope: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = self._qualified(node.name)
        if node.returns is not None:
            self._add(node.name, qualified, self._expr(node.returns), "return", node, 1.0, "annotation")
        arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        for argument in arguments:
            if argument.annotation is not None:
                self._add(
                    argument.arg,
                    f"{qualified}.{argument.arg}",
                    self._expr(argument.annotation),
                    "parameter",
                    argument,
                    1.0,
                    "annotation",
                )
        if node.args.vararg and node.args.vararg.annotation is not None:
            argument = node.args.vararg
            self._add(argument.arg, f"{qualified}.{argument.arg}", self._expr(argument.annotation), "parameter", argument, 1.0, "annotation")
        if node.args.kwarg and node.args.kwarg.annotation is not None:
            argument = node.args.kwarg
            self._add(argument.arg, f"{qualified}.{argument.arg}", self._expr(argument.annotation), "parameter", argument, 1.0, "annotation")
        self._scope.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self._scope.pop()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        for name in self._target_names(node.target):
            self._add(name, self._qualified(name), self._expr(node.annotation), "variable", node, 1.0, "annotation")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        inferred = self._infer_value(node.value)
        if inferred is not None:
            type_name, confidence, source = inferred
            for target in node.targets:
                for name in self._target_names(target):
                    self._add(name, self._qualified(name), type_name, "variable", node, confidence, source)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        inferred = self._infer_value(node.value)
        if inferred is not None:
            type_name, confidence, source = inferred
            for name in self._target_names(node.target):
                self._add(name, self._qualified(name), type_name, "variable", node, confidence, source)
        self.generic_visit(node)

    def _infer_value(self, node: ast.AST) -> tuple[str, float, str] | None:
        if isinstance(node, ast.Constant):
            value = node.value
            if value is None:
                return ("None", 1.0, "literal")
            return (type(value).__name__, 1.0, "literal")
        if isinstance(node, ast.List):
            return ("list", 0.95, "literal")
        if isinstance(node, ast.Tuple):
            return ("tuple", 0.95, "literal")
        if isinstance(node, ast.Set):
            return ("set", 0.95, "literal")
        if isinstance(node, ast.Dict):
            return ("dict", 0.95, "literal")
        if isinstance(node, ast.Lambda):
            return ("Callable", 0.9, "lambda")
        if isinstance(node, ast.Call):
            target = self._expr(node.func)
            if target:
                return (target, 0.8, "constructor_or_factory")
        return None

    def _add(
        self,
        symbol: str,
        qualified_symbol: str,
        type_name: str,
        kind: str,
        node: ast.AST,
        confidence: float,
        source: str,
    ) -> None:
        cleaned = type_name.strip()
        if not cleaned:
            return
        self.records.append(
            ResolvedType(
                symbol=symbol,
                qualified_symbol=qualified_symbol,
                type_name=cleaned,
                kind=kind,
                path=self.path,
                line=int(getattr(node, "lineno", 1)),
                confidence=float(confidence),
                source=source,
            )
        )

    def _qualified(self, name: str) -> str:
        return ".".join((*self._scope, name)) if self._scope else name

    @staticmethod
    def _expr(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return getattr(node, "id", getattr(node, "attr", ""))

    @staticmethod
    def _target_names(node: ast.AST) -> tuple[str, ...]:
        if isinstance(node, ast.Name):
            return (node.id,)
        if isinstance(node, ast.Attribute):
            try:
                return (ast.unparse(node),)
            except Exception:
                return (node.attr,)
        if isinstance(node, (ast.Tuple, ast.List)):
            names: list[str] = []
            for item in node.elts:
                names.extend(_TypeVisitor._target_names(item))
            return tuple(names)
        return ()
