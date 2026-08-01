"""Dependency-aware planning for incremental global symbol graph updates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .dependency_resolver import DependencyResolver


@dataclass(frozen=True, slots=True)
class SymbolGraphUpdatePlan:
    """Stable set of files whose resolved symbol bindings must be refreshed."""

    changed: tuple[Path, ...]
    removed: tuple[Path, ...]
    rebind: tuple[Path, ...]

    def snapshot(self) -> dict[str, list[str]]:
        """Return a deterministic JSON-native representation of this plan."""
        return {
            "changed": [str(path) for path in self.changed],
            "removed": [str(path) for path in self.removed],
            "rebind": [str(path) for path in self.rebind],
        }


class SymbolGraphUpdatePlanner:
    """Preserve dependency impact across graph mutations.

    Impact is captured both before and after dependency updates. This prevents
    removed imports, moved modules and rewritten package edges from losing the
    files that depended on the previous graph shape.
    """

    def __init__(self, project_root: str | Path, resolver: DependencyResolver) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._resolver = resolver
        self._changed: set[Path] = set()
        self._removed: set[Path] = set()
        self._pre_impact: set[Path] = set()
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    def pending_snapshot(self) -> dict[str, list[str]]:
        return {
            "changed": [str(path) for path in self._ordered(self._changed)],
            "removed": [str(path) for path in self._ordered(self._removed)],
            "pre_impact": [str(path) for path in self._ordered(self._pre_impact)],
        }

    def capture_before(self, paths: Iterable[str | Path] | str | Path) -> bool:
        """Capture transitive dependents before dependency edges are mutated."""
        if isinstance(paths, (str, Path)):
            path_values = (paths,)
        else:
            try:
                path_values = tuple(paths)
            except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError):
                return False
        changed_before = set(self._changed)
        impact_before = set(self._pre_impact)
        for value in path_values:
            try:
                path = self._resolve_path(value)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            if not self._inside_root(path):
                continue
            self._changed.add(path)
            self._pre_impact.update(self._affected(path))
        return self._changed != changed_before or self._pre_impact != impact_before

    def mark_removed(self, path: str | Path) -> bool:
        try:
            candidate = self._resolve_path(path)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        if not self._inside_root(candidate):
            return False
        before = (candidate in self._removed, candidate in self._changed)
        self._removed.add(candidate)
        self._changed.add(candidate)
        return before != (True, True)

    def finalize(self) -> SymbolGraphUpdatePlan:
        """Build a deterministic plan and reset this planner for the next batch."""
        post_impact: set[Path] = set()
        for path in self._changed:
            post_impact.update(self._affected(path))
        rebind = (self._pre_impact | post_impact | self._changed) - self._removed
        plan = SymbolGraphUpdatePlan(
            changed=self._ordered(self._changed),
            removed=self._ordered(self._removed),
            rebind=self._ordered(path for path in rebind if self._safe_is_file(path)),
        )
        self._changed.clear()
        self._removed.clear()
        self._pre_impact.clear()
        if plan.changed or plan.removed or plan.rebind:
            self._revision += 1
        return plan

    def _affected(self, path: Path) -> set[Path]:
        try:
            values = self._resolver.affected_files(
                path,
                include_source=True,
                transitive=True,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return set()
        result: set[Path] = set()
        if values is None:
            return result
        value_iterable = (values,) if isinstance(values, (str, Path)) else values
        try:
            staged_values = tuple(value_iterable)
        except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError):
            return result
        for value in staged_values:
            try:
                candidate = self._resolve_path(value)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            if self._inside_root(candidate):
                result.add(candidate)
        return result

    @staticmethod
    def _safe_is_file(path: Path) -> bool:
        try:
            return path.is_file()
        except (OSError, RuntimeError, ValueError):
            return False

    def _resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve(strict=False)

    def _inside_root(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _ordered(paths: Iterable[Path]) -> tuple[Path, ...]:
        return tuple(sorted(set(paths), key=lambda item: str(item).casefold()))
