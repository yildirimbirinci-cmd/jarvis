"""Bind indexed symbol references to project-wide symbol definitions."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterable

from .project_symbol_registry import ProjectSymbol
from .project_symbol_resolver import ProjectSymbolResolver
from .symbol_reference_parser import SymbolReferenceRecord


@dataclass(frozen=True, slots=True)
class ResolvedSymbolReference:
    """One reference together with its best project definition candidates."""

    reference: SymbolReferenceRecord
    definitions: tuple[ProjectSymbol, ...]
    resolved_query: str | None = None
    ambiguous: bool = False

    @property
    def resolved(self) -> bool:
        return bool(self.definitions) and not self.ambiguous

    @property
    def primary_definition(self) -> ProjectSymbol | None:
        return self.definitions[0] if self.definitions else None


@dataclass(frozen=True, slots=True)
class ReferenceBindingResult:
    """Deterministic reference-binding result for one source file."""

    path: str
    references: tuple[ResolvedSymbolReference, ...]

    @property
    def resolved_count(self) -> int:
        return sum(1 for item in self.references if item.resolved)

    @property
    def ambiguous_count(self) -> int:
        return sum(1 for item in self.references if item.ambiguous)

    @property
    def unresolved_count(self) -> int:
        return sum(1 for item in self.references if not item.definitions)


class CrossFileReferenceResolver:
    """Resolve reference records with import, module and lexical-scope context."""

    def __init__(self, project_root: str | Path, symbol_resolver: ProjectSymbolResolver) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._symbol_resolver = symbol_resolver
        self._cache: dict[str, tuple[ResolvedSymbolReference, ...]] = {}
        self._revision = 0
        self._lock = RLock()

    def _resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path is outside project root: {path}") from exc
        return resolved

    @staticmethod
    def _bounded_limit(value: object, *, default: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            parsed = default
        return max(1, min(parsed, maximum))

    @staticmethod
    def _path_key(path: Path) -> str:
        return str(path).casefold()

    def invalidate(self, path: str | Path) -> bool:
        key = self._path_key(self._resolve_path(path))
        with self._lock:
            changed = self._cache.pop(key, None) is not None
            if changed:
                self._revision += 1
            return changed

    def clear(self) -> bool:
        with self._lock:
            if not self._cache:
                return False
            self._cache.clear()
            self._revision += 1
            return True

    def bind_file(
        self,
        path: str | Path,
        references: Iterable[SymbolReferenceRecord],
        *,
        limit_per_reference: int = 25,
    ) -> ReferenceBindingResult:
        resolved_source = self._resolve_path(path)
        result = self._build_binding_result(
            resolved_source,
            references,
            limit_per_reference=limit_per_reference,
        )
        with self._lock:
            key = self._path_key(resolved_source)
            if self._cache.get(key) != result.references:
                self._cache[key] = result.references
                self._revision += 1
        return result

    def _build_binding_result(
        self,
        resolved_source: Path,
        references: Iterable[SymbolReferenceRecord],
        *,
        limit_per_reference: int,
    ) -> ReferenceBindingResult:
        source = str(resolved_source)
        bounded = self._bounded_limit(limit_per_reference, default=25, maximum=250)
        if isinstance(references, (str, bytes, Path)):
            raise TypeError("references must be an iterable of SymbolReferenceRecord values")
        try:
            reference_values = tuple(references)
        except (TypeError, RuntimeError, RecursionError, MemoryError) as exc:
            raise ValueError("references iterable failed during binding") from exc
        if any(not isinstance(item, SymbolReferenceRecord) for item in reference_values):
            raise TypeError("references must contain only SymbolReferenceRecord values")

        bound: list[ResolvedSymbolReference] = []
        seen_references: set[tuple[str, int, int, str, str | None]] = set()
        for reference in reference_values:
            reference_key = (
                reference.name, reference.line, reference.column, reference.context, reference.scope
            )
            if reference_key in seen_references:
                continue
            seen_references.add(reference_key)
            resolution = self._symbol_resolver.resolve(
                reference.name,
                source_path=source,
                scope=reference.scope,
                limit=bounded,
            )
            bound.append(
                ResolvedSymbolReference(
                    reference=reference,
                    definitions=tuple(dict.fromkeys(resolution.definitions)),
                    resolved_query=resolution.resolved_query,
                    ambiguous=resolution.ambiguous,
                )
            )
        ordered = tuple(
            sorted(
                bound,
                key=lambda item: (
                    item.reference.line,
                    item.reference.column,
                    item.reference.name.casefold(),
                    item.reference.context,
                ),
            )
        )
        return ReferenceBindingResult(source, ordered)

    def rebind_files(
        self,
        paths: Iterable[str | Path],
        reference_provider,
        *,
        limit_per_reference: int = 25,
    ) -> tuple[ReferenceBindingResult, ...]:
        """Rebind existing source files after project definitions change.

        ``reference_provider`` must return the already indexed references for a
        path. Missing/deleted paths are removed from the in-memory binding cache.
        """
        if isinstance(paths, (str, Path)):
            paths = (paths,)
        elif isinstance(paths, bytes):
            raise TypeError("paths must contain only text or pathlib.Path values")
        try:
            path_values = tuple(paths)
        except (TypeError, RuntimeError, RecursionError, MemoryError) as exc:
            raise ValueError("paths iterable failed during rebinding") from exc
        if any(not isinstance(path, (str, Path)) for path in path_values):
            raise TypeError("paths must contain only text or pathlib.Path values")
        ordered_paths = sorted(
            {self._resolve_path(path) for path in path_values},
            key=lambda item: str(item).casefold(),
        )

        pending: list[tuple[str, tuple[ResolvedSymbolReference, ...]]] = []
        removed_keys: list[str] = []
        results: list[ReferenceBindingResult] = []
        try:
            for source in ordered_paths:
                key = self._path_key(source)
                if not source.is_file():
                    removed_keys.append(key)
                    continue
                result = self._build_binding_result(
                    source,
                    reference_provider(source),
                    limit_per_reference=limit_per_reference,
                )
                pending.append((key, result.references))
                results.append(result)
        except (LookupError, OSError, RuntimeError, TypeError, ValueError, RecursionError, MemoryError) as exc:
            raise ValueError("reference provider failed during rebinding") from exc

        with self._lock:
            changed = False
            for key in removed_keys:
                changed = self._cache.pop(key, None) is not None or changed
            for key, references in pending:
                if self._cache.get(key) != references:
                    self._cache[key] = references
                    changed = True
            if changed:
                self._revision += 1
        return tuple(results)

    def bindings_for_file(self, path: str | Path) -> tuple[ResolvedSymbolReference, ...]:
        key = self._path_key(self._resolve_path(path))
        with self._lock:
            return self._cache.get(key, ())

    def bindings_to(self, canonical_name: str, *, limit: int = 1000) -> tuple[ResolvedSymbolReference, ...]:
        if not isinstance(canonical_name, str):
            return ()
        query = canonical_name.strip().casefold()
        if not query:
            return ()
        with self._lock:
            values = tuple(item for group in self._cache.values() for item in group)
        matches = [
            item
            for item in values
            if any(
                definition.canonical_name.casefold() == query
                or definition.qualified_name.casefold() == query
                for definition in item.definitions
            )
        ]
        matches.sort(
            key=lambda item: (
                item.reference.path.casefold(),
                item.reference.line,
                item.reference.column,
            )
        )
        return tuple(matches[: self._bounded_limit(limit, default=1000, maximum=10000)])

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "revision": self._revision,
                "files": {
                    key: [
                        {
                            "name": item.reference.name,
                            "path": item.reference.path,
                            "line": item.reference.line,
                            "column": item.reference.column,
                            "context": item.reference.context,
                            "scope": item.reference.scope,
                            "resolved_query": item.resolved_query,
                            "ambiguous": item.ambiguous,
                            "definitions": [definition.canonical_name for definition in item.definitions],
                        }
                        for item in values
                    ]
                    for key, values in sorted(self._cache.items())
                },
            }

    def stats(self) -> dict[str, int]:
        with self._lock:
            values = tuple(item for group in self._cache.values() for item in group)
            file_count = len(self._cache)
        return {
            "bound_references": len(values),
            "resolved_references": sum(1 for item in values if item.resolved),
            "ambiguous_references": sum(1 for item in values if item.ambiguous),
            "unresolved_references": sum(1 for item in values if not item.definitions),
            "reference_binding_files": file_count,
            "reference_binding_revision": self.revision,
        }
