"""Thread-safe directed dependency graph for incremental SAE indexing."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterable

try:
    from artmach_assistant.core.path_normalizer import normalize_path, path_key
except ModuleNotFoundError:
    from core.path_normalizer import normalize_path, path_key


@dataclass(frozen=True, slots=True)
class DependencyGraphStats:
    nodes: int = 0
    edges: int = 0
    revision: int = 0


class DependencyGraph:
    """Store source-file dependencies and resolve reverse impact sets."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._forward: dict[str, set[str]] = {}
        self._reverse: dict[str, set[str]] = {}
        self._display_paths: dict[str, str] = {}
        self._revision = 0

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def replace_dependencies(
        self,
        source: str | Path,
        dependencies: Iterable[str | Path] | str | Path,
    ) -> bool:
        source_path = self._normalize(source)
        source_key = self._key(source_path)
        if dependencies is None or isinstance(dependencies, (bytes, bytearray, memoryview)):
            raise TypeError("dependencies must be a path or an iterable of paths")
        values = (dependencies,) if isinstance(dependencies, (str, Path)) else dependencies
        try:
            normalized = {self._normalize(item) for item in tuple(values)}
        except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError) as exc:
            raise ValueError("dependencies iterable failed") from exc
        dependency_keys = {self._key(item) for item in normalized if self._key(item) != source_key}
        display_updates = {self._key(item): str(item) for item in normalized}

        with self._lock:
            previous = self._forward.get(source_key, set())
            changed = (
                previous != dependency_keys
                or self._display_paths.get(source_key) != str(source_path)
                or any(self._display_paths.get(key) != value for key, value in display_updates.items())
            )
            if not changed:
                return False

            removed = previous - dependency_keys
            for key in removed:
                dependents = self._reverse.get(key)
                if dependents is not None:
                    dependents.discard(source_key)
                    if not dependents:
                        self._reverse.pop(key, None)

            self._display_paths[source_key] = str(source_path)
            self._display_paths.update(display_updates)
            self._forward[source_key] = set(dependency_keys)
            for key in dependency_keys:
                self._reverse.setdefault(key, set()).add(source_key)
            for key in removed:
                self._drop_display_path_if_orphaned(key)
            self._revision += 1
            return True

    def remove(self, source: str | Path) -> bool:
        source_key = self._key(self._normalize(source))
        with self._lock:
            if source_key not in self._forward and source_key not in self._reverse and source_key not in self._display_paths:
                return False
            outgoing = self._forward.pop(source_key, set())
            for key in outgoing:
                dependents = self._reverse.get(key)
                if dependents is not None:
                    dependents.discard(source_key)
                    if not dependents:
                        self._reverse.pop(key, None)
            incoming = self._reverse.pop(source_key, set())
            for key in incoming:
                dependencies = self._forward.get(key)
                if dependencies is not None:
                    dependencies.discard(source_key)
            self._display_paths.pop(source_key, None)
            for key in outgoing:
                self._drop_display_path_if_orphaned(key)
            self._revision += 1
            return True

    def dependencies_of(self, source: str | Path) -> tuple[str, ...]:
        key = self._key(self._normalize(source))
        with self._lock:
            return tuple(sorted(self._display_paths[item] for item in self._forward.get(key, set())))

    def direct_dependents_of(self, source: str | Path) -> tuple[str, ...]:
        key = self._key(self._normalize(source))
        with self._lock:
            return tuple(sorted(self._display_paths[item] for item in self._reverse.get(key, set())))

    def affected_by(
        self,
        source: str | Path,
        *,
        include_source: bool = True,
        transitive: bool = True,
    ) -> tuple[str, ...]:
        source_path = self._normalize(source)
        source_key = self._key(source_path)
        with self._lock:
            seen: set[str] = {source_key} if include_source else set()
            queue = deque(self._reverse.get(source_key, set()))
            while queue:
                current = queue.popleft()
                if current in seen:
                    continue
                seen.add(current)
                if transitive:
                    queue.extend(self._reverse.get(current, set()))
            return tuple(sorted(self._display_paths.get(item, str(source_path) if item == source_key else item) for item in seen))

    def to_dict(self) -> dict[str, list[str]]:
        with self._lock:
            return {
                self._display_paths.get(source, source): sorted(
                    self._display_paths.get(item, item) for item in dependencies
                )
                for source, dependencies in sorted(self._forward.items())
            }

    def load_dict(self, payload: dict[str, list[str]]) -> bool:
        """Atomically replace graph contents from a validated snapshot."""
        if not isinstance(payload, dict):
            raise TypeError("dependency graph payload must be a dictionary")
        replacement = DependencyGraph()
        for source, dependencies in payload.items():
            if not isinstance(source, str) or not source.strip():
                raise ValueError("dependency graph source paths must be non-empty strings")
            if not isinstance(dependencies, list):
                raise ValueError("dependency graph dependency lists must be arrays")
            if any(not isinstance(item, str) or not item.strip() for item in dependencies):
                raise ValueError("dependency graph dependencies must be non-empty strings")
            replacement.replace_dependencies(source, dependencies)

        snapshot = replacement.to_dict()
        with self._lock:
            if snapshot == self.to_dict():
                return False
            self._forward = {key: set(values) for key, values in replacement._forward.items()}
            self._reverse = {key: set(values) for key, values in replacement._reverse.items()}
            self._display_paths = dict(replacement._display_paths)
            self._revision += 1
            return True

    def clear(self) -> bool:
        with self._lock:
            if not self._forward and not self._reverse and not self._display_paths:
                return False
            self._forward.clear()
            self._reverse.clear()
            self._display_paths.clear()
            self._revision += 1
            return True

    def stats(self) -> DependencyGraphStats:
        with self._lock:
            return DependencyGraphStats(
                nodes=len(set(self._forward) | set(self._reverse)),
                edges=sum(len(items) for items in self._forward.values()),
                revision=self._revision,
            )

    def _drop_display_path_if_orphaned(self, key: str) -> None:
        if key not in self._forward and key not in self._reverse:
            self._display_paths.pop(key, None)

    @staticmethod
    def _normalize(path: str | Path) -> Path:
        return normalize_path(path)

    @staticmethod
    def _key(path: Path) -> str:
        return path_key(path)
