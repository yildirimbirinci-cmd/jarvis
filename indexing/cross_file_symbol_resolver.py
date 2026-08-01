"""Import-aware cross-file symbol resolution for Python projects."""
from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from .project_symbol_registry import ProjectSymbol, ProjectSymbolRegistry


@dataclass(frozen=True, slots=True)
class CrossFileSymbolResolution:
    query: str
    source_path: str | None
    scope: str | None
    matches: tuple[ProjectSymbol, ...]
    ambiguous: bool
    resolved_query: str | None = None


@dataclass(frozen=True, slots=True)
class _ImportContext:
    aliases: dict[str, str]
    star_modules: tuple[str, ...]


class CrossFileSymbolResolver:
    def __init__(self, project_root: str | Path, registry: ProjectSymbolRegistry) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._registry = registry
        self._import_cache: dict[str, tuple[int, int, _ImportContext]] = {}
        self._revision = 0
        self._lock = RLock()

    def invalidate(self, path: str | Path) -> bool:
        if not isinstance(path, (str, Path)):
            raise TypeError("path must be str or Path")
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path is outside project root: {path}") from exc
        key = str(resolved).casefold()
        with self._lock:
            changed = self._import_cache.pop(key, None) is not None
            if changed:
                self._revision += 1
            return changed

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def clear(self) -> bool:
        with self._lock:
            if not self._import_cache:
                return False
            self._import_cache.clear()
            self._revision += 1
            return True

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "revision": self._revision,
                "files": {
                    key: {
                        "mtime_ns": value[0],
                        "size": value[1],
                        "aliases": dict(sorted(value[2].aliases.items())),
                        "star_modules": list(value[2].star_modules),
                    }
                    for key, value in sorted(self._import_cache.items())
                },
            }

    def resolve(self, query: str, *, source_path: str | Path | None = None,
                scope: str | None = None, limit: int = 100) -> CrossFileSymbolResolution:
        if not isinstance(query, str):
            raise TypeError("query must be text")
        if scope is not None and not isinstance(scope, str):
            raise TypeError("scope must be text or None")
        text = query.strip(); source = self._normalize_source(source_path)
        try:
            bounded = int(limit)
        except (TypeError, ValueError, OverflowError):
            bounded = 100
        bounded = max(1, min(bounded, 10_000))
        if not text:
            return CrossFileSymbolResolution("", source, scope, (), False, None)
        targets = self._candidate_queries(text, source)
        candidates: list[ProjectSymbol] = []
        seen: set[tuple[str, int, int]] = set()
        for target in targets:
            lookup_limit = min(max(bounded * 4, 100), 10_000)
            found = self._registry.canonical(target, limit=lookup_limit) or self._registry.exact(target, limit=lookup_limit)
            for item in found:
                key = (item.path, item.line, item.column)
                if key not in seen: seen.add(key); candidates.append(item)
        if not candidates:
            candidates.extend(self._registry.search(text, limit=min(max(bounded * 4, 100), 10_000)))
        ranked = sorted(candidates, key=lambda item: self._rank(item, text, targets, source, scope))
        matches = tuple(ranked[:bounded])
        ambiguous = len(matches) > 1 and self._rank(matches[0], text, targets, source, scope)[:6] == self._rank(matches[1], text, targets, source, scope)[:6]
        return CrossFileSymbolResolution(text, source, scope, matches, ambiguous, targets[0] if targets else text)

    def _candidate_queries(self, query: str, source: str | None) -> tuple[str, ...]:
        values: list[str] = []
        if source:
            context = self._imports_for(Path(source))
            head, dot, tail = query.partition(".")
            if head in context.aliases:
                values.append(context.aliases[head] + (f".{tail}" if dot else ""))
            if not dot and query in context.aliases:
                values.append(context.aliases[query])
            if not dot:
                values.extend(f"{module}.{query}" for module in context.star_modules)
            module = self._module_for(Path(source))
            if module:
                package = module if Path(source).name == "__init__.py" else module.rpartition(".")[0]
                if package: values.append(f"{package}.{query}")
                values.append(f"{module}.{query}")
        values.append(query)
        return tuple(dict.fromkeys(item for item in values if item))

    def _imports_for(self, path: Path) -> _ImportContext:
        try: stat = path.stat(); stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError: return _ImportContext({}, ())
        key = str(path.resolve(strict=False)).casefold()
        with self._lock:
            cached = self._import_cache.get(key)
            if cached and cached[:2] == stamp: return cached[2]
        aliases: dict[str, str] = {}; stars: list[str] = []
        try:
            with tokenize.open(path) as handle:
                source = handle.read()
            tree = ast.parse(source, filename=key)
        except (OSError, UnicodeError, SyntaxError, RecursionError, MemoryError):
            # Editors often leave a file temporarily unreadable or syntactically
            # incomplete. Preserve the last known-good import context until a
            # successful parse replaces it; a deleted file is still handled by
            # the stat failure above.
            return cached[2] if cached else _ImportContext({}, ())
        current_module = self._module_for(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".", 1)[0]
                    aliases[local] = alias.name if alias.asname else local
            elif isinstance(node, ast.ImportFrom):
                module = self._resolve_relative(current_module, node.module or "", node.level, path.name == "__init__.py")
                for alias in node.names:
                    if alias.name == "*":
                        if module: stars.append(module)
                    else:
                        aliases[alias.asname or alias.name] = f"{module}.{alias.name}" if module else alias.name
        result = _ImportContext(aliases, tuple(dict.fromkeys(stars)))
        with self._lock:
            updated = (*stamp, result)
            if self._import_cache.get(key) != updated:
                self._import_cache[key] = updated
                self._revision += 1
        return result

    @staticmethod
    def _resolve_relative(current: str, module: str, level: int, is_package: bool) -> str:
        if not level: return module
        parts = current.split(".") if current else []
        if not is_package and parts: parts.pop()
        trim = max(0, level - 1)
        if trim: parts = parts[:-trim] if trim <= len(parts) else []
        if module: parts.extend(module.split("."))
        return ".".join(parts)

    def _rank(self, symbol: ProjectSymbol, query: str, targets: tuple[str, ...], source: str | None,
              scope: str | None) -> tuple[int, int, int, int, int, int, str, int]:
        canonical = symbol.canonical_name.casefold(); q = query.casefold(); target_cf = tuple(x.casefold() for x in targets)
        imported = next((i for i, item in enumerate(target_cf) if canonical == item), len(target_cf) + 1)
        exact = 0 if canonical == q else 1 if symbol.qualified_name.casefold() == q else 2 if symbol.name.casefold() == q else 3
        same_file = 0 if source and Path(symbol.path) == Path(source) else 1
        same_module, distance = 1, 999
        if source:
            same_module = 0 if symbol.module == self._module_for(Path(source)) else 1
            distance = _path_distance(Path(source).parent, Path(symbol.path).parent)
        scope_rank = 0 if scope and symbol.qualified_name.casefold().startswith(scope.casefold().strip(".")) else 1
        return (imported, exact, same_file, same_module, scope_rank, distance, symbol.path.casefold(), symbol.line)

    def _normalize_source(self, source_path: str | Path | None) -> str | None:
        if source_path is None:
            return None
        if not isinstance(source_path, (str, Path)):
            raise TypeError("source_path must be str, Path, or None")
        candidate = Path(source_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"source path is outside project root: {source_path}") from exc
        return str(resolved)

    def _module_for(self, path: Path) -> str:
        try: relative = path.resolve(strict=False).relative_to(self.root)
        except ValueError: return ""
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__": parts.pop()
        return ".".join(parts)


def _path_distance(left: Path, right: Path) -> int:
    a, b = left.resolve(strict=False).parts, right.resolve(strict=False).parts
    shared = 0
    for x, y in zip(a, b):
        if x.casefold() != y.casefold(): break
        shared += 1
    return len(a) - shared + len(b) - shared
