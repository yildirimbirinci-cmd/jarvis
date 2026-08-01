"""Python import resolver for incremental SAE dependency indexing."""

from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterable

try:
    from artmach_assistant.core.path_normalizer import normalize_project_root, path_key, project_path
except ModuleNotFoundError:
    from core.path_normalizer import normalize_project_root, path_key, project_path

from .dependency_graph import DependencyGraph


@dataclass(frozen=True, slots=True)
class DependencyScanResult:
    path: str
    dependencies: tuple[str, ...]
    parse_error: str | None = None


class DependencyResolver:
    """Build and query a project-local dependency graph from Python imports."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        graph: DependencyGraph | None = None,
        source_suffixes: Iterable[str] = (".py", ".pyi"),
    ) -> None:
        root = normalize_project_root(project_root)
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        self._project_root = root
        self._graph = graph or DependencyGraph()
        suffix_values = (source_suffixes,) if isinstance(source_suffixes, str) else source_suffixes
        self._suffixes = frozenset(self._normalize_suffix(item) for item in suffix_values)
        self._lock = RLock()
        self._module_to_path: dict[str, Path] = {}
        self._path_to_module: dict[str, str] = {}

    @property
    def graph(self) -> DependencyGraph:
        return self._graph

    def load_graph(self, payload: dict[str, list[str]]) -> bool:
        """Load a persisted graph without exposing a partially refreshed state."""
        with self._lock:
            old_module_to_path = dict(self._module_to_path)
            old_path_to_module = dict(self._path_to_module)
            try:
                self._refresh_module_map()
                return self._graph.load_dict(payload)
            except Exception:
                self._module_to_path = old_module_to_path
                self._path_to_module = old_path_to_module
                raise

    def graph_snapshot(self) -> dict[str, list[str]]:
        return self._graph.to_dict()

    @property
    def graph_revision(self) -> int:
        return self._graph.revision

    def source_paths(self) -> tuple[Path, ...]:
        """Return known project source paths for cooperating incremental indexes."""
        with self._lock:
            return tuple(sorted(self._module_to_path.values(), key=lambda item: str(item).casefold()))

    def rebuild(self) -> tuple[DependencyScanResult, ...]:
        """Rebuild into a staging graph and publish only the complete result."""
        with self._lock:
            old_module_to_path = dict(self._module_to_path)
            old_path_to_module = dict(self._path_to_module)
            original_graph = self._graph
            staging_graph = DependencyGraph()
            try:
                self._refresh_module_map()
                self._graph = staging_graph
                results = [self._scan_known_path(path) for path in self._module_to_path.values()]
            except Exception:
                self._module_to_path = old_module_to_path
                self._path_to_module = old_path_to_module
                raise
            finally:
                self._graph = original_graph
            original_graph.load_dict(staging_graph.to_dict())
            return tuple(sorted(results, key=lambda item: item.path.casefold()))

    def update_file(self, path: str | Path) -> DependencyScanResult:
        candidate = self._resolve_project_path(path)
        with self._lock:
            if not candidate.is_file() or candidate.suffix.casefold() not in self._suffixes:
                self._graph.remove(candidate)
                self._remove_from_module_map(candidate)
                return DependencyScanResult(str(candidate), ())

            previous_module = self._path_to_module.get(self._key(candidate))
            self._register_path(candidate)
            result = self._scan_known_path(candidate)

            current_module = self._path_to_module.get(self._key(candidate))
            if current_module and current_module != previous_module:
                self._rescan_potential_importers(current_module, exclude=candidate)
            elif current_module and previous_module is None:
                self._rescan_potential_importers(current_module, exclude=candidate)
            return result

    def remove_file(self, path: str | Path) -> tuple[str, ...]:
        """Remove a source file while preserving its impact set for callers."""

        candidate = self._resolve_project_path(path)
        with self._lock:
            affected = self._graph.affected_by(
                candidate,
                include_source=True,
                transitive=True,
            )
            self._graph.remove(candidate)
            self._remove_from_module_map(candidate)
            return affected

    def affected_files(
        self,
        path: str | Path,
        *,
        include_source: bool = True,
        transitive: bool = True,
    ) -> tuple[str, ...]:
        return self._graph.affected_by(
            self._resolve_project_path(path),
            include_source=include_source,
            transitive=transitive,
        )

    def _scan_known_path(self, path: Path) -> DependencyScanResult:
        try:
            with tokenize.open(path) as handle:
                source = handle.read()
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError, RecursionError, MemoryError) as exc:
            self._graph.replace_dependencies(path, ())
            return DependencyScanResult(str(path), (), f"{type(exc).__name__}: {exc}")

        dependencies: set[Path] = set()
        current_module = self._path_to_module.get(self._key(path), "")
        is_package_init = path.stem == "__init__"
        package_parts = current_module.split(".") if is_package_init else current_module.split(".")[:-1]

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = self._resolve_module(alias.name)
                    if resolved is not None:
                        dependencies.add(resolved)
            elif isinstance(node, ast.ImportFrom):
                base = self._resolve_relative_module(package_parts, node.level, node.module)
                candidates = [base] if base else []
                if base:
                    candidates.extend(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
                for module_name in candidates:
                    resolved = self._resolve_module(module_name)
                    if resolved is not None:
                        dependencies.add(resolved)

        dependencies.discard(path)
        self._graph.replace_dependencies(path, dependencies)
        return DependencyScanResult(
            path=str(path),
            dependencies=tuple(sorted(str(item) for item in dependencies)),
        )

    def _refresh_module_map(self) -> None:
        self._module_to_path.clear()
        self._path_to_module.clear()
        candidates = (
            path
            for path in self._project_root.rglob("*")
            if path.is_file() and path.suffix.casefold() in self._suffixes
        )
        for path in sorted(candidates, key=self._module_map_sort_key):
            self._register_path(path)

    def _register_path(self, path: Path) -> None:
        try:
            relative = path.relative_to(self._project_root)
        except ValueError:
            return
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if not parts:
            return
        module = ".".join(parts)
        current = self._module_to_path.get(module)
        if current is not None and self._module_map_sort_key(current) <= self._module_map_sort_key(path):
            return
        if current is not None:
            self._path_to_module.pop(self._key(current), None)
        self._module_to_path[module] = path
        self._path_to_module[self._key(path)] = module

    def _module_map_sort_key(self, path: Path) -> tuple[int, str]:
        try:
            relative = path.relative_to(self._project_root)
        except ValueError:
            relative = path
        package_priority = 0 if path.stem == "__init__" else 1
        return package_priority, str(relative).casefold()

    def _remove_from_module_map(self, path: Path) -> None:
        key = self._key(path)
        module = self._path_to_module.pop(key, None)
        if module is not None and self._module_to_path.get(module) == path:
            self._module_to_path.pop(module, None)

    def _rescan_potential_importers(self, module_name: str, *, exclude: Path) -> None:
        """Reconnect imports that were unresolved before a module appeared."""

        top_level = module_name.split(".", 1)[0]
        exclude_key = self._key(exclude)
        for candidate in tuple(self._module_to_path.values()):
            if self._key(candidate) == exclude_key or not candidate.is_file():
                continue
            try:
                with tokenize.open(candidate) as handle:
                    source = handle.read()
            except (OSError, UnicodeError, SyntaxError):
                continue
            if module_name in source or top_level in source:
                self._scan_known_path(candidate)

    def _resolve_module(self, module_name: str) -> Path | None:
        candidate = module_name
        while candidate:
            path = self._module_to_path.get(candidate)
            if path is not None:
                return path
            candidate = candidate.rpartition(".")[0]
        return None

    @staticmethod
    def _resolve_relative_module(
        package_parts: list[str],
        level: int,
        module: str | None,
    ) -> str:
        if level <= 0:
            return module or ""
        keep = max(0, len(package_parts) - level + 1)
        parts = package_parts[:keep]
        if module:
            parts.extend(module.split("."))
        return ".".join(parts)


    def _resolve_project_path(self, path: str | Path) -> Path:
        return project_path(self._project_root, path, require_inside=True)

    @staticmethod
    def _normalize_suffix(value: str) -> str:
        suffix = str(value).strip().casefold()
        if not suffix:
            raise ValueError("source suffix cannot be empty")
        return suffix if suffix.startswith(".") else f".{suffix}"

    @staticmethod
    def _key(path: Path) -> str:
        return path_key(path)
