"""Incremental project-wide symbol registry synchronized with SymbolIndex."""
from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Iterable

try:
    from ..core.path_normalizer import normalize_project_root, project_path
except ImportError:  # Support the historical top-level ``indexing`` import.
    from core.path_normalizer import normalize_project_root, project_path

from .project_symbol_registry import ProjectSymbol, ProjectSymbolRegistry
from .symbol_index import SymbolIndex


class ProjectSymbolIndex:
    """Adds module-aware project lookup without duplicating symbol parsing."""

    def __init__(self, project_root: str | Path, symbol_index: SymbolIndex) -> None:
        self.root = normalize_project_root(project_root)
        self._symbol_index = symbol_index
        self._registry = ProjectSymbolRegistry(self.root)
        self._lock = RLock()

    @property
    def registry(self) -> ProjectSymbolRegistry:
        return self._registry

    def rebuild(self, paths: Iterable[str | Path]) -> bool:
        """Build a replacement registry first, then publish it atomically."""
        if paths is None or isinstance(paths, (str, bytes, bytearray, memoryview, Path)):
            raise TypeError("paths must be an iterable of path values")
        try:
            raw_paths = tuple(paths)
        except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError) as exc:
            raise ValueError("unable to materialize paths") from exc
        if any(not isinstance(path, (str, Path)) for path in raw_paths):
            raise TypeError("paths must contain only str or Path values")

        candidates = tuple(self._normalize_path(path) for path in raw_paths)
        replacement = ProjectSymbolRegistry(self.root)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            replacement.replace_file(candidate, self._symbol_index.symbols_for_file(candidate))

        with self._lock:
            if replacement.snapshot()["files"] == self._registry.snapshot()["files"]:
                return False
            self._registry = replacement
            return True

    def refresh_file(self, path: str | Path) -> bool:
        candidate = self._normalize_path(path)
        with self._lock:
            if not candidate.is_file():
                return self._registry.remove_file(candidate)
            return self._registry.replace_file(
                candidate,
                self._symbol_index.symbols_for_file(candidate),
            )

    def remove_file(self, path: str | Path) -> bool:
        with self._lock:
            return self._registry.remove_file(self._normalize_path(path))

    def resolve(self, query: str, *, limit: int = 100) -> tuple[ProjectSymbol, ...]:
        exact = self._registry.exact(query, limit=limit)
        if exact:
            return exact
        return self._registry.search(query, limit=limit)

    def snapshot(self) -> dict[str, object]:
        return self._registry.snapshot()

    def stats(self) -> dict[str, int]:
        return self._registry.stats()

    def _normalize_path(self, path: str | Path) -> Path:
        return project_path(self.root, path, require_inside=True)
