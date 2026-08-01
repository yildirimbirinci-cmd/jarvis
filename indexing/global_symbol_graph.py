"""Project-wide symbol graph built from resolved definitions and references."""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from .cross_file_reference_resolver import ReferenceBindingResult, ResolvedSymbolReference
from .project_symbol_registry import ProjectSymbol, ProjectSymbolRegistry


@dataclass(frozen=True, slots=True)
class GlobalSymbolEdge:
    source_path: str
    source_name: str
    source_line: int
    target_canonical_name: str
    target_path: str
    target_line: int
    kind: str
    confidence: float




@dataclass(frozen=True, slots=True)
class GlobalSymbolGraphValidation:
    checked_edges: int
    removed_edges: int
    duplicate_edges: int
    missing_source_files: int
    missing_target_files: int
    missing_target_symbols: int
    repaired: bool

    @property
    def valid(self) -> bool:
        return self.removed_edges == 0


class GlobalSymbolGraph:
    """Incremental, deterministic graph of project symbols and bound references."""

    def __init__(self, project_root: str | Path, registry: ProjectSymbolRegistry) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._registry = registry
        self._edges_by_file: dict[str, tuple[GlobalSymbolEdge, ...]] = {}
        self._incoming: dict[str, tuple[GlobalSymbolEdge, ...]] = {}
        self._outgoing: dict[str, tuple[GlobalSymbolEdge, ...]] = {}
        self._lock = RLock()
        self._revision = 0

    def replace_file(self, result: ReferenceBindingResult) -> bool:
        """Replace one file and report whether the graph actually changed."""
        return self.replace_files((result,))

    def replace_files(self, results: Iterable[ReferenceBindingResult]) -> bool:
        """Apply many file bindings atomically and rebuild lookups only once."""
        try:
            staged_results = tuple(results)
        except (MemoryError, RecursionError, RuntimeError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("results iterable failed") from exc

        prepared: dict[str, tuple[GlobalSymbolEdge, ...]] = {}
        for result in staged_results:
            if not isinstance(result, ReferenceBindingResult):
                raise TypeError("results must contain ReferenceBindingResult instances")
            path = str(self._resolve_path(result.path))
            if not self._is_inside_root(path):
                raise ValueError(f"source path is outside project root: {path}")

            edges: dict[tuple[str, int, str, str, int], GlobalSymbolEdge] = {}
            for item in result.references:
                if not isinstance(item, ResolvedSymbolReference):
                    raise TypeError("references must contain ResolvedSymbolReference instances")
                reference = item.reference
                for symbol in item.definitions:
                    if not isinstance(symbol, ProjectSymbol):
                        raise TypeError("definitions must contain ProjectSymbol instances")
                    target_path = str(self._resolve_path(symbol.path))
                    if not self._is_inside_root(target_path):
                        continue
                    edge = GlobalSymbolEdge(
                        source_path=path,
                        source_name=reference.name,
                        source_line=reference.line,
                        target_canonical_name=symbol.canonical_name,
                        target_path=target_path,
                        target_line=symbol.line,
                        kind=reference.context,
                        confidence=1.0 if item.resolved else 0.5,
                    )
                    edges.setdefault(_edge_key(edge), edge)
            prepared[path] = tuple(sorted(edges.values(), key=_edge_key))
        if not prepared:
            return False
        with self._lock:
            changed = any(self._edges_by_file.get(path) != edges for path, edges in prepared.items())
            if not changed:
                return False
            self._edges_by_file.update(prepared)
            self._revision += 1
            self._rebuild_lookups()
            return True

    def remove_file(self, path: str | Path) -> bool:
        return self.remove_files((path,))

    def remove_files(self, paths: Iterable[str | Path] | str | Path) -> bool:
        """Remove source/target edges for many files in one atomic update."""
        path_values = (paths,) if isinstance(paths, (str, Path)) else paths
        removed = {str(self._resolve_path(path)) for path in path_values}
        if not removed:
            return False
        with self._lock:
            changed = False
            for absolute in removed:
                if self._edges_by_file.pop(absolute, None) is not None:
                    changed = True
            for source, values in tuple(self._edges_by_file.items()):
                filtered = tuple(edge for edge in values if edge.target_path not in removed)
                if filtered != values:
                    self._edges_by_file[source] = filtered
                    changed = True
            if changed:
                self._revision += 1
                self._rebuild_lookups()
            return changed

    def clear(self) -> bool:
        with self._lock:
            if not self._edges_by_file:
                return False
            self._edges_by_file.clear()
            self._incoming.clear()
            self._outgoing.clear()
            self._revision += 1
            return True

    def symbol(self, canonical_name: str) -> ProjectSymbol | None:
        values = self._registry.canonical(canonical_name, limit=1)
        return values[0] if values else None

    @staticmethod
    def _safe_limit(value: object, default: int = 1000) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            parsed = default
        return max(1, min(parsed, 10_000))

    def incoming(self, canonical_name: str, *, limit: int = 1000) -> tuple[GlobalSymbolEdge, ...]:
        if not isinstance(canonical_name, str):
            return ()
        query = canonical_name.strip().casefold()
        if not query:
            return ()
        with self._lock:
            return self._incoming.get(query, ())[:self._safe_limit(limit)]

    def outgoing_for_file(self, path: str | Path, *, limit: int = 1000) -> tuple[GlobalSymbolEdge, ...]:
        absolute = str(self._resolve_path(path)).casefold()
        with self._lock:
            return self._outgoing.get(absolute, ())[:self._safe_limit(limit)]

    def related_symbols(self, canonical_name: str, *, limit: int = 200) -> tuple[ProjectSymbol, ...]:
        edges = self.incoming(canonical_name, limit=limit)
        seen: set[str] = set()
        result: list[ProjectSymbol] = []
        for edge in edges:
            for symbol in self._registry.symbols_for_file(edge.source_path):
                key = symbol.canonical_name.casefold()
                if key not in seen:
                    seen.add(key)
                    result.append(symbol)
        return tuple(result[:self._safe_limit(limit, default=200)])


    def snapshot(self) -> dict[str, Any]:
        """Return a deterministic JSON-serializable graph snapshot."""
        with self._lock:
            return {
                "revision": self._revision,
                "edges_by_file": {
                    path: [
                        {
                            "source_path": edge.source_path,
                            "source_name": edge.source_name,
                            "source_line": edge.source_line,
                            "target_canonical_name": edge.target_canonical_name,
                            "target_path": edge.target_path,
                            "target_line": edge.target_line,
                            "kind": edge.kind,
                            "confidence": edge.confidence,
                        }
                        for edge in values
                    ]
                    for path, values in sorted(self._edges_by_file.items(), key=lambda item: item[0].casefold())
                }
            }

    def load_snapshot(self, payload: dict[str, Any]) -> bool:
        """Restore a persisted graph only when its structure is internally valid."""
        raw_files = payload.get("edges_by_file")
        if not isinstance(raw_files, dict):
            return False
        restored: dict[str, tuple[GlobalSymbolEdge, ...]] = {}
        restored_keys: set[str] = set()
        try:
            revision = max(0, int(payload.get("revision", 0)))
            for raw_path, raw_edges in raw_files.items():
                if not isinstance(raw_path, str) or not isinstance(raw_edges, list):
                    return False
                path = str(self._resolve_path(raw_path))
                normalized_path = os.path.normcase(path)
                if normalized_path in restored_keys or not self._is_inside_root(path):
                    return False
                restored_keys.add(normalized_path)
                edges: list[GlobalSymbolEdge] = []
                seen: set[tuple[str, int, str, str, int]] = set()
                for item in raw_edges:
                    if not isinstance(item, dict):
                        return False
                    confidence = float(item["confidence"])
                    if not math.isfinite(confidence):
                        return False
                    edge = GlobalSymbolEdge(
                        source_path=str(
                            Path(str(item["source_path"])).expanduser().resolve(strict=False)
                        ),
                        source_name=str(item["source_name"]),
                        source_line=max(0, int(item["source_line"])),
                        target_canonical_name=str(item["target_canonical_name"]),
                        target_path=str(
                            Path(str(item["target_path"])).expanduser().resolve(strict=False)
                        ),
                        target_line=max(0, int(item["target_line"])),
                        kind=str(item["kind"]),
                        confidence=max(0.0, min(1.0, confidence)),
                    )
                    if os.path.normcase(edge.source_path) != normalized_path:
                        return False
                    edge = GlobalSymbolEdge(
                        source_path=path,
                        source_name=edge.source_name,
                        source_line=edge.source_line,
                        target_canonical_name=edge.target_canonical_name,
                        target_path=edge.target_path,
                        target_line=edge.target_line,
                        kind=edge.kind,
                        confidence=edge.confidence,
                    )
                    if not self._is_inside_root(edge.target_path):
                        return False
                    key = _edge_key(edge)
                    if key not in seen:
                        seen.add(key)
                        edges.append(edge)
                restored[path] = tuple(sorted(edges, key=_edge_key))
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):
            return False
        with self._lock:
            self._edges_by_file = restored
            self._revision = revision
            self._rebuild_lookups()
        return True

    def _resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve(strict=False)

    def _is_inside_root(self, path: str | Path) -> bool:
        try:
            self._resolve_path(path).relative_to(self.root)
            return True
        except ValueError:
            return False

    def validate(self, *, repair: bool = False) -> GlobalSymbolGraphValidation:
        """Validate graph edges against the filesystem and current symbol registry."""
        with self._lock:
            checked = removed = duplicates = missing_source = missing_target = missing_symbol = 0
            cleaned: dict[str, tuple[GlobalSymbolEdge, ...]] = {}
            for source_path, values in self._edges_by_file.items():
                source_exists = Path(source_path).is_file() and self._is_inside_root(source_path)
                seen: set[tuple[str, int, str, str, int]] = set()
                kept: list[GlobalSymbolEdge] = []
                for edge in values:
                    checked += 1
                    key = _edge_key(edge)
                    invalid = False
                    if key in seen:
                        duplicates += 1
                        invalid = True
                    else:
                        seen.add(key)
                    if not source_exists or edge.source_path != source_path:
                        missing_source += 1
                        invalid = True
                    if not Path(edge.target_path).is_file() or not self._is_inside_root(edge.target_path):
                        missing_target += 1
                        invalid = True
                    symbols = self._registry.canonical(edge.target_canonical_name, limit=100)
                    if not any(symbol.path == edge.target_path and symbol.line == edge.target_line for symbol in symbols):
                        missing_symbol += 1
                        invalid = True
                    if invalid:
                        removed += 1
                    else:
                        kept.append(edge)
                if kept or (source_exists and not values):
                    cleaned[source_path] = tuple(sorted(kept, key=_edge_key))
            graph_changed = cleaned != self._edges_by_file
            if repair and graph_changed:
                self._edges_by_file = cleaned
                self._revision += 1
                self._rebuild_lookups()
            return GlobalSymbolGraphValidation(
                checked_edges=checked,
                removed_edges=removed,
                duplicate_edges=duplicates,
                missing_source_files=missing_source,
                missing_target_files=missing_target,
                missing_target_symbols=missing_symbol,
                repaired=bool(repair and graph_changed),
            )

    def stats(self) -> dict[str, int]:
        with self._lock:
            edges = sum(len(items) for items in self._edges_by_file.values())
            return {
                "global_symbol_nodes": self._registry.stats()["project_symbols"],
                "global_symbol_edges": edges,
                "global_symbol_files": len(self._edges_by_file),
                "global_symbol_revision": self._revision,
            }

    def _rebuild_lookups(self) -> None:
        incoming: dict[str, list[GlobalSymbolEdge]] = {}
        outgoing: dict[str, list[GlobalSymbolEdge]] = {}
        for values in self._edges_by_file.values():
            for edge in values:
                incoming.setdefault(edge.target_canonical_name.casefold(), []).append(edge)
                outgoing.setdefault(edge.source_path.casefold(), []).append(edge)
        self._incoming = {key: tuple(sorted(value, key=_edge_key)) for key, value in incoming.items()}
        self._outgoing = {key: tuple(sorted(value, key=_edge_key)) for key, value in outgoing.items()}


def _edge_key(edge: GlobalSymbolEdge) -> tuple[str, int, str, str, int]:
    return (edge.source_path.casefold(), edge.source_line, edge.target_canonical_name.casefold(), edge.target_path.casefold(), edge.target_line)
