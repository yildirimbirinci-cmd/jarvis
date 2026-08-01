"""Shared immutable data models for semantic analysis.

The module intentionally contains no parser or index dependencies.  Later
semantic-analysis stages can exchange deterministic, serialisable values
without importing executable project code.
"""
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

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping


class ScopeKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    LAMBDA = "lambda"
    COMPREHENSION = "comprehension"


class SymbolKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    PARAMETER = "parameter"
    VARIABLE = "variable"
    ATTRIBUTE = "attribute"
    IMPORT = "import"
    TYPE_ALIAS = "type_alias"


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def _text(value: object, *, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    if "\x00" in normalized:
        raise ValueError(f"{field_name} must not contain NUL characters")
    return normalized


def _line(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 1:
        raise ValueError(f"{field_name} must be at least 1")
    return value


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("confidence must be numeric")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError("confidence must be finite")
    if not 0.0 <= result <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return result


def _metadata(values: Mapping[str, object] | None) -> Mapping[str, object]:
    if values is None:
        return MappingProxyType({})
    if not isinstance(values, Mapping):
        raise TypeError("metadata must be a mapping")
    normalized: dict[str, object] = {}
    for key, value in values.items():
        normalized[_text(key, field_name="metadata key")] = value
    return MappingProxyType(normalized)


@dataclass(frozen=True, slots=True)
class SourceLocation:
    path: str
    line: int
    column: int = 0
    end_line: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        path = _text(self.path, field_name="path")
        line = _line(self.line, field_name="line")
        if isinstance(self.column, bool) or not isinstance(self.column, int):
            raise TypeError("column must be an integer")
        if self.column < 0:
            raise ValueError("column must be non-negative")
        end_line = line if self.end_line is None else _line(self.end_line, field_name="end_line")
        end_column = self.column if self.end_column is None else self.end_column
        if isinstance(end_column, bool) or not isinstance(end_column, int):
            raise TypeError("end_column must be an integer")
        if end_column < 0:
            raise ValueError("end_column must be non-negative")
        if (end_line, end_column) < (line, self.column):
            raise ValueError("source range must not end before it starts")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "line", line)
        object.__setattr__(self, "end_line", end_line)
        object.__setattr__(self, "end_column", end_column)

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        line: int,
        column: int = 0,
        end_line: int | None = None,
        end_column: int | None = None,
    ) -> "SourceLocation":
        return cls(str(Path(path).expanduser().resolve(strict=False)), line, column, end_line, end_column)


@dataclass(frozen=True, slots=True)
class SemanticType:
    name: str
    arguments: tuple["SemanticType", ...] = ()
    nullable: bool = False
    confidence: float = 1.0
    source: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, field_name="type name"))
        if isinstance(self.arguments, (str, bytes)):
            raise TypeError("type arguments must be an iterable of SemanticType")
        arguments = tuple(self.arguments)
        if not all(isinstance(item, SemanticType) for item in arguments):
            raise TypeError("all type arguments must be SemanticType instances")
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        object.__setattr__(self, "source", _text(self.source, field_name="type source"))

    @property
    def display_name(self) -> str:
        value = self.name
        if self.arguments:
            value = f"{value}[{', '.join(item.display_name for item in self.arguments)}]"
        return f"{value} | None" if self.nullable and self.name != "None" else value


@dataclass(frozen=True, slots=True)
class SemanticSymbol:
    name: str
    qualified_name: str
    kind: SymbolKind
    location: SourceLocation
    scope_id: str
    inferred_type: SemanticType | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, field_name="symbol name"))
        object.__setattr__(self, "qualified_name", _text(self.qualified_name, field_name="qualified name"))
        object.__setattr__(self, "scope_id", _text(self.scope_id, field_name="scope_id"))
        if not isinstance(self.kind, SymbolKind):
            object.__setattr__(self, "kind", SymbolKind(self.kind))
        if not isinstance(self.location, SourceLocation):
            raise TypeError("location must be a SourceLocation")
        if self.inferred_type is not None and not isinstance(self.inferred_type, SemanticType):
            raise TypeError("inferred_type must be a SemanticType or None")
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class SemanticScope:
    id: str
    name: str
    kind: ScopeKind
    location: SourceLocation
    parent_id: str | None = None
    symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, field_name="scope id"))
        object.__setattr__(self, "name", _text(self.name, field_name="scope name"))
        if not isinstance(self.kind, ScopeKind):
            object.__setattr__(self, "kind", ScopeKind(self.kind))
        if not isinstance(self.location, SourceLocation):
            raise TypeError("location must be a SourceLocation")
        if self.parent_id is not None:
            object.__setattr__(self, "parent_id", _text(self.parent_id, field_name="parent_id"))
        if isinstance(self.symbols, (str, bytes)):
            raise TypeError("symbols must be an iterable of qualified names")
        symbols = tuple(dict.fromkeys(_text(item, field_name="symbol reference") for item in self.symbols))
        object.__setattr__(self, "symbols", symbols)


@dataclass(frozen=True, slots=True)
class SemanticDiagnostic:
    code: str
    message: str
    severity: DiagnosticSeverity
    location: SourceLocation | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _text(self.code, field_name="diagnostic code"))
        object.__setattr__(self, "message", _text(self.message, field_name="diagnostic message"))
        if not isinstance(self.severity, DiagnosticSeverity):
            object.__setattr__(self, "severity", DiagnosticSeverity(self.severity))
        if self.location is not None and not isinstance(self.location, SourceLocation):
            raise TypeError("location must be a SourceLocation or None")
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class SemanticAnalysisResult:
    path: str
    scopes: tuple[SemanticScope, ...] = ()
    symbols: tuple[SemanticSymbol, ...] = ()
    diagnostics: tuple[SemanticDiagnostic, ...] = ()
    parse_error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _text(self.path, field_name="result path"))
        object.__setattr__(self, "scopes", self._typed_tuple(self.scopes, SemanticScope, "scopes"))
        object.__setattr__(self, "symbols", self._typed_tuple(self.symbols, SemanticSymbol, "symbols"))
        object.__setattr__(self, "diagnostics", self._typed_tuple(self.diagnostics, SemanticDiagnostic, "diagnostics"))
        if self.parse_error is not None:
            object.__setattr__(self, "parse_error", _text(self.parse_error, field_name="parse_error"))
        scope_ids = [item.id for item in self.scopes]
        if len(scope_ids) != len(set(scope_ids)):
            raise ValueError("scope ids must be unique")
        known_scopes = set(scope_ids)
        for scope in self.scopes:
            if scope.parent_id is not None and scope.parent_id not in known_scopes:
                raise ValueError(f"unknown parent scope: {scope.parent_id}")
        for symbol in self.symbols:
            if symbol.scope_id not in known_scopes:
                raise ValueError(f"unknown symbol scope: {symbol.scope_id}")

    @staticmethod
    def _typed_tuple(values: Iterable[object], expected: type, field_name: str) -> tuple:
        if isinstance(values, (str, bytes)):
            raise TypeError(f"{field_name} must be an iterable")
        result = tuple(values)
        if not all(isinstance(item, expected) for item in result):
            raise TypeError(f"all {field_name} entries must be {expected.__name__} instances")
        return result

    @property
    def has_errors(self) -> bool:
        return self.parse_error is not None or any(
            item.severity is DiagnosticSeverity.ERROR for item in self.diagnostics
        )

    def symbols_in_scope(self, scope_id: str) -> tuple[SemanticSymbol, ...]:
        query = _text(scope_id, field_name="scope_id")
        return tuple(item for item in self.symbols if item.scope_id == query)

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
