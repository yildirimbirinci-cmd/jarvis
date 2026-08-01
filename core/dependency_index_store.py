from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from artmach_assistant.config import DATA_DIR
from artmach_assistant.core.path_normalizer import normalize_project_root, path_key
from artmach_assistant.core.store_validation import atomic_write_json, read_json_object, require_schema_version


class DependencyIndexStore:
    """Persists dependency graphs outside source trees using atomic writes."""

    SCHEMA_VERSION = 1
    MAX_CACHE_BYTES = 128 * 1024 * 1024

    def __init__(self, directory: str | Path | None = None) -> None:
        if directory is None:
            resolved = DATA_DIR / "dependency_indexes"
        elif not isinstance(directory, (str, Path)):
            raise TypeError("Dependency index directory must be path-like.")
        else:
            text = str(directory).strip()
            if not text or "\x00" in text:
                raise ValueError("Dependency index directory must be non-empty.")
            resolved = Path(directory).expanduser()
        self.directory = resolved.resolve(strict=False)

    def _path_for(self, root: str | Path) -> Path:
        normalized = path_key(root).encode("utf-8", errors="replace")
        digest = hashlib.sha256(normalized).hexdigest()[:24]
        return self.directory / f"{digest}.json"

    @staticmethod
    def _normalize_root(root: str | Path) -> Path:
        return normalize_project_root(root)

    @staticmethod
    def _discard_invalid_snapshot(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
        except OSError:
            pass

    @classmethod
    def _validate_graph(cls, graph: Any) -> dict[str, list[str]] | None:
        if not isinstance(graph, dict):
            return None
        validated: dict[str, list[str]] = {}
        for source, dependencies in graph.items():
            if not isinstance(source, str) or not source.strip() or "\x00" in source:
                return None
            if not isinstance(dependencies, list):
                return None
            normalized_dependencies: list[str] = []
            seen: set[str] = set()
            for dependency in dependencies:
                if not isinstance(dependency, str) or not dependency.strip() or "\x00" in dependency:
                    return None
                key = os.path.normcase(os.path.normpath(dependency))
                if key in seen:
                    continue
                seen.add(key)
                normalized_dependencies.append(dependency)
            validated[source] = normalized_dependencies
        return validated

    def load(self, root: str | Path) -> dict[str, list[str]] | None:
        try:
            resolved_root = self._normalize_root(root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        path = self._path_for(resolved_root)
        if path.is_symlink() or not path.is_file():
            self._discard_invalid_snapshot(path)
            return None
        try:
            payload = read_json_object(path, max_bytes=self.MAX_CACHE_BYTES)
            require_schema_version(payload, field="schema_version", expected=self.SCHEMA_VERSION)
            cached_root_value = payload.get("root")
            if not isinstance(cached_root_value, str) or not cached_root_value.strip() or "\x00" in cached_root_value:
                raise ValueError("Invalid cached root")
            cached_root = self._normalize_root(Path(cached_root_value))
            if path_key(cached_root) != path_key(resolved_root):
                raise ValueError("Cached root mismatch")
            graph = self._validate_graph(payload.get("graph"))
            if graph is None:
                raise ValueError("Invalid dependency graph")
            return graph
        except (OSError, UnicodeError, ValueError, TypeError):
            self._discard_invalid_snapshot(path)
            return None

    def save(self, root: str | Path, graph: dict[str, list[str]]) -> Path:
        validated_graph = self._validate_graph(graph)
        if validated_graph is None:
            raise ValueError("Dependency graph must be a mapping of non-empty string paths to string lists.")
        resolved_root = self._normalize_root(root)
        target = self._path_for(resolved_root)
        if target.is_symlink():
            raise OSError("Refusing to overwrite a symbolic-link dependency index snapshot.")
        atomic_write_json(
            target,
            {"schema_version": self.SCHEMA_VERSION, "root": str(resolved_root), "graph": validated_graph},
            max_bytes=self.MAX_CACHE_BYTES,
        )
        return target

    def remove(self, root: str | Path) -> None:
        try:
            resolved_root = self._normalize_root(root)
            self._path_for(resolved_root).unlink(missing_ok=True)
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
