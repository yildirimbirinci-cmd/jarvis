"""Incremental semantic graph facade for Python workspaces."""
from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Iterable

from .semantic_graph_builder import SemanticBuildResult, SemanticGraphBuilder
from .semantic_graph_database import SemanticGraphDatabase


class SemanticGraph:
    def __init__(
        self,
        project_root: str | Path,
        *,
        suffixes: Iterable[str] = (".py", ".pyi"),
    ) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        if not self.root.is_dir():
            raise NotADirectoryError(str(self.root))
        try:
            suffix_values = (suffixes,) if isinstance(suffixes, str) else tuple(suffixes)
        except (TypeError, ValueError, RuntimeError, MemoryError, RecursionError) as exc:
            raise ValueError("suffixes iterable failed") from exc
        self._suffixes = frozenset(
            value
            for item in suffix_values
            if (value := self._normalize_suffix(item))
        )
        if not self._suffixes:
            raise ValueError("at least one source suffix is required")
        self._builder = SemanticGraphBuilder()
        self._database = SemanticGraphDatabase(self.root)
        self._lock = RLock()

    @property
    def revision(self) -> int:
        return self._database.revision

    def rebuild(self, paths: Iterable[str | Path]) -> tuple[SemanticBuildResult, ...]:
        """Parse all candidates first and publish the graph only on full success."""
        if paths is None or isinstance(paths, (bytes, bytearray, memoryview)):
            raise TypeError("paths must be an iterable of paths")
        try:
            candidates = (paths,) if isinstance(paths, (str, Path)) else tuple(paths)
        except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError) as exc:
            raise ValueError("paths iterable failed: unable to materialize paths") from exc
        results: list[SemanticBuildResult] = []
        replacements: list[tuple[Path, tuple, tuple]] = []
        with self._lock:
            for path in candidates:
                try:
                    candidate = self._resolve_path(path)
                except (OSError, RuntimeError, TypeError, ValueError):
                    continue
                if candidate.suffix.casefold() not in self._suffixes:
                    continue
                if not self._inside_root(candidate) or not self._safe_is_file(candidate):
                    continue
                result = self._builder.parse_file(candidate)
                results.append(result)
                if result.parse_error is not None:
                    return self._ordered_results(results)
                replacements.append((candidate, result.nodes, result.edges))
            self._database.replace_all(replacements)
        return self._ordered_results(results)

    def update_file(self, path: str | Path) -> SemanticBuildResult | None:
        try:
            candidate = self._resolve_path(path)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if candidate.suffix.casefold() not in self._suffixes or not self._inside_root(candidate):
            return None
        with self._lock:
            if not self._safe_is_file(candidate):
                self._database.remove_file(candidate)
                return SemanticBuildResult(str(candidate), (), ())
            try:
                result = self._builder.parse_file(candidate)
            except (OSError, RuntimeError, TypeError, ValueError, MemoryError, RecursionError) as exc:
                return SemanticBuildResult(
                    str(candidate), (), (), f"{type(exc).__name__}: {exc}"
                )
            if result.parse_error is None:
                self._database.replace_file(candidate, result.nodes, result.edges)
            return result

    def remove_file(self, path: str | Path) -> bool:
        try:
            candidate = self._resolve_path(path)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        if not self._inside_root(candidate):
            return False
        with self._lock:
            return self._database.remove_file(candidate)

    def clear(self) -> bool:
        with self._lock:
            return self._database.clear()

    def snapshot(self) -> dict[str, object]:
        return self._database.snapshot()

    def integrity_check(self) -> bool:
        return self._database.integrity_check()

    def references_to(self, target, *, kinds=(), limit=500):
        return self._database.edges_for_target(target, kinds=kinds, limit=limit)

    def stats(self) -> dict[str, int]:
        values = self._database.stats()
        return {
            key: value
            for key, value in values.items()
            if key != "semantic_revision"
        }

    def _resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve(strict=False)

    @staticmethod
    def _normalize_suffix(value: object) -> str:
        try:
            suffix = str(value).strip().casefold()
        except (TypeError, ValueError, RuntimeError, UnicodeError, RecursionError):
            return ""
        if not suffix:
            return ""
        return suffix if suffix.startswith(".") else f".{suffix}"

    def _inside_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _safe_is_file(path: Path) -> bool:
        try:
            return path.is_file()
        except (OSError, RuntimeError, ValueError):
            return False

    @staticmethod
    def _ordered_results(
        values: Iterable[SemanticBuildResult],
    ) -> tuple[SemanticBuildResult, ...]:
        return tuple(sorted(values, key=lambda item: item.path.casefold()))
