"""Public project symbol resolution facade."""
from __future__ import annotations
from dataclasses import dataclass
import math
from pathlib import Path
from .cross_file_symbol_resolver import CrossFileSymbolResolution, CrossFileSymbolResolver
from .project_symbol_registry import ProjectSymbol
from .type_resolver import ResolvedType, TypeIndex

@dataclass(frozen=True, slots=True)
class ProjectSymbolResolution:
    query: str
    definitions: tuple[ProjectSymbol, ...]
    types: tuple[ResolvedType, ...]
    source_path: str | None = None
    scope: str | None = None
    ambiguous: bool = False
    resolved_query: str | None = None

class ProjectSymbolResolver:
    def __init__(self, project_root: str | Path, cross_file_resolver: CrossFileSymbolResolver, type_index: TypeIndex | None = None) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        if not self.root.is_dir():
            raise NotADirectoryError(str(self.root))
        if cross_file_resolver is None or not callable(getattr(cross_file_resolver, "resolve", None)):
            raise TypeError("cross_file_resolver must provide resolve()")
        self._cross_file_resolver = cross_file_resolver; self._type_index = type_index

    def invalidate(self, path: str | Path) -> bool:
        """Invalidate a backend entry without allowing backend failures to escape."""
        method = getattr(self._cross_file_resolver, "invalidate", None)
        if not callable(method):
            return False
        try:
            return bool(method(path))
        except (LookupError, OSError, RuntimeError, TypeError, ValueError):
            return False
        return True

    def resolve(self, query: str, *, source_path: str | Path | None = None, scope: str | None = None, limit: int = 100) -> ProjectSymbolResolution:
        text = query.strip() if isinstance(query, str) else ""
        if not text:
            return ProjectSymbolResolution("", (), (), None, None, False, None)
        bounded = _safe_limit(limit)
        try:
            result = self._cross_file_resolver.resolve(
                text, source_path=source_path, scope=scope, limit=bounded
            )
        except (LookupError, OSError, RuntimeError, TypeError, ValueError):
            return ProjectSymbolResolution(text, (), (), str(source_path) if source_path is not None else None, scope, False, None)
        if not isinstance(result, CrossFileSymbolResolution):
            return ProjectSymbolResolution(text, (), (), str(source_path) if source_path is not None else None, scope, False, None)
        types: list[ResolvedType] = []
        seen: set[tuple[str, str, int, str]] = set()
        if self._type_index is not None:
            names = [text]
            if result.resolved_query:
                names.append(result.resolved_query)
            names.extend(item.name for item in result.matches)
            names.extend(item.qualified_name for item in result.matches)
            for name in dict.fromkeys(item.strip() for item in names if isinstance(item, str) and item.strip()):
                try:
                    resolved = self._type_index.resolve(name, limit=bounded)
                    if isinstance(resolved, (str, bytes, Path)):
                        continue
                    candidates = tuple(resolved)
                except (LookupError, OSError, RuntimeError, TypeError, ValueError):
                    continue
                for item in candidates:
                    if not isinstance(item, ResolvedType):
                        continue
                    key = (item.qualified_symbol, item.path, item.line, item.type_name)
                    if key not in seen:
                        seen.add(key)
                        types.append(item)
        return ProjectSymbolResolution(
            result.query, result.matches, tuple(types[:bounded]), result.source_path,
            result.scope, result.ambiguous, result.resolved_query
        )


def _safe_limit(value: object, *, default: int = 100) -> int:
    """Return a finite positive limit without leaking conversion errors."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(numeric):
        return default
    return max(1, min(int(numeric), 10_000))
