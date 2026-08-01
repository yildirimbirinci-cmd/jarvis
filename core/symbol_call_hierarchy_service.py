"""Read-only call hierarchy queries backed by the SAE call graph."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterator

from artmach_assistant.core.query_validation import bounded_positive_int, normalized_query

from artmach_assistant.indexing import (
    CallGraph,
    CallGraphEdge,
    CrossFileReferenceResolver,
    SymbolIndex,
    SymbolRecord,
    SymbolReferenceIndex,
    SymbolReferenceRecord,
)


@dataclass(frozen=True, slots=True)
class SymbolCaller:
    """One indexed call site and its best matching enclosing symbol."""

    reference: SymbolReferenceRecord
    enclosing_symbol: SymbolRecord | None = None


@dataclass(frozen=True, slots=True)
class SymbolCallHierarchyResult:
    """Definition, incoming-call and outgoing-call information for a symbol."""

    query: str
    definitions: tuple[SymbolRecord, ...]
    callers: tuple[SymbolCaller, ...]
    caller_edges: tuple[CallGraphEdge, ...] = ()
    callee_edges: tuple[CallGraphEdge, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.definitions or self.callers or self.caller_edges or self.callee_edges)


class SymbolCallHierarchyService:
    """Serve deterministic caller/callee views without executing workspace code."""

    def __init__(
        self,
        project_root: str | Path,
        symbol_index: SymbolIndex,
        reference_index: SymbolReferenceIndex,
        resolved_reference_index: CrossFileReferenceResolver | None = None,
        call_graph: CallGraph | None = None,
    ) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._symbol_index = symbol_index
        self._reference_index = reference_index
        self._resolved_reference_index = resolved_reference_index
        self._call_graph = call_graph
        self._lock = RLock()

    def callers(self, name: str, *, limit: int = 500) -> SymbolCallHierarchyResult:
        return self.hierarchy(name, limit=limit, include_callees=False)

    def callees(self, name: str, *, limit: int = 500) -> SymbolCallHierarchyResult:
        return self.hierarchy(name, limit=limit, include_callers=False)

    def hierarchy(
        self,
        name: str,
        *,
        limit: int = 500,
        include_callers: bool = True,
        include_callees: bool = True,
    ) -> SymbolCallHierarchyResult:
        query = normalized_query(name)
        if not query:
            return SymbolCallHierarchyResult("", (), ())

        bounded = bounded_positive_int(limit, default=500, maximum=5000)
        with self._lock:
            definitions = self._definitions(query, bounded)
            canonical_names = self._canonical_names(query, definitions)

            caller_edges = self._graph_edges(canonical_names, incoming=True, limit=bounded) if include_callers else ()
            callee_edges = self._graph_edges(canonical_names, incoming=False, limit=bounded) if include_callees else ()
            callers = (
                self._legacy_callers(query, canonical_names, bounded)
                if include_callers
                else ()
            )

        return SymbolCallHierarchyResult(
            query=query,
            definitions=definitions,
            callers=callers,
            caller_edges=caller_edges,
            callee_edges=callee_edges,
        )


    def _canonical_names(
        self, query: str, definitions: tuple[SymbolRecord, ...]
    ) -> tuple[str, ...]:
        values: list[str] = []
        for item in definitions:
            canonical = self._canonical_name(item)
            if canonical:
                values.append(canonical)
        values.append(query)
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = self._safe_text(value)
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            unique.append(normalized)
        return tuple(unique)

    def _definitions(self, query: str, limit: int) -> tuple[SymbolRecord, ...]:
        short_name = query.rsplit(".", 1)[-1]
        candidate_limit = max(1000, limit)
        snapshots: dict[
            tuple[str, int, int, str],
            tuple[SymbolRecord, str, str, str],
        ] = {}
        for value in dict.fromkeys((query, short_name)):
            try:
                candidates = self._symbol_index.search(value, limit=candidate_limit)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            for item in self._safe_iter(candidates):
                try:
                    raw_path = getattr(item, "path", "")
                    line = int(getattr(item, "line", 0))
                    column = int(getattr(item, "column", 0))
                    name = str(getattr(item, "name", ""))
                    qualified_name = str(getattr(item, "qualified_name", ""))
                    key = (
                        str(raw_path).casefold(),
                        line,
                        column,
                        qualified_name.casefold(),
                    )
                    canonical_name = self._canonical_name_from_parts(
                        raw_path, qualified_name
                    )
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    continue
                snapshots.setdefault(
                    key, (item, name, qualified_name, canonical_name)
                )
        matches = (
            (key, snapshot[0])
            for key, snapshot in snapshots.items()
            if snapshot[1] == query
            or snapshot[2] == query
            or snapshot[3] == query
        )
        return tuple(item for _, item in sorted(matches, key=lambda value: value[0]))[:limit]

    def _canonical_name(self, item: SymbolRecord) -> str:
        try:
            raw_path = getattr(item, "path", "")
            qualified_name = str(getattr(item, "qualified_name", ""))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ""
        return self._canonical_name_from_parts(raw_path, qualified_name)

    def _canonical_name_from_parts(self, raw_path: object, qualified_name: str) -> str:
        qualified_name = qualified_name.strip()
        if not raw_path or not qualified_name:
            return ""
        try:
            path = Path(str(raw_path)).expanduser()
            absolute = (
                path.resolve(strict=False)
                if path.is_absolute()
                else (self.root / path).resolve(strict=False)
            )
            relative = absolute.relative_to(self.root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return ""
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        module = ".".join(parts)
        return f"{module}.{qualified_name}" if module else qualified_name

    def _graph_edges(
        self,
        canonical_names: tuple[str, ...],
        *,
        incoming: bool,
        limit: int,
    ) -> tuple[CallGraphEdge, ...]:
        if self._call_graph is None:
            return ()
        values: list[tuple[tuple[str, int, int, str, str], CallGraphEdge]] = []
        seen: set[tuple[str, int, int, str, str]] = set()
        for canonical_name in canonical_names:
            try:
                edges = (
                    self._call_graph.callers(canonical_name, limit=limit)
                    if incoming
                    else self._call_graph.callees(canonical_name, limit=limit)
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            for edge in self._safe_iter(edges):
                try:
                    key = (
                        str(edge.caller_path),
                        int(edge.call_line),
                        int(edge.call_column),
                        str(edge.callee_canonical_name),
                        str(edge.callee_path),
                    )
                except (AttributeError, TypeError, ValueError):
                    continue
                normalized_key = (
                    key[0].casefold(), key[1], key[2],
                    key[3].casefold(), key[4].casefold(),
                )
                if normalized_key not in seen:
                    seen.add(normalized_key)
                    values.append((normalized_key, edge))
        values.sort(key=lambda item: item[0])
        return tuple(edge for _, edge in values[:limit])

    def _legacy_callers(
        self,
        query: str,
        canonical_names: tuple[str, ...],
        limit: int,
    ) -> tuple[SymbolCaller, ...]:
        short_name = query.rsplit(".", 1)[-1]
        if self._resolved_reference_index is not None:
            resolved_call_sites: list[tuple[tuple[str, int, int, str], SymbolReferenceRecord]] = []
            resolved_seen: set[tuple[str, int, int, str]] = set()
            for canonical_name in canonical_names:
                try:
                    resolved = self._resolved_reference_index.bindings_to(
                        canonical_name, limit=limit
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    continue
                for item in self._safe_iter(resolved):
                    try:
                        reference = getattr(item, "reference", None)
                        context = (
                            getattr(reference, "context", None)
                            if reference is not None
                            else None
                        )
                        if reference is None or context != "call":
                            continue
                        path = str(reference.path)
                        line = int(reference.line)
                        column = int(reference.column)
                        scope = str(reference.scope or "")
                    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                        continue
                    normalized_key = (path.casefold(), line, column, scope.casefold())
                    if normalized_key in resolved_seen:
                        continue
                    resolved_seen.add(normalized_key)
                    resolved_call_sites.append(
                        ((path.casefold(), line, column, scope.casefold()), reference)
                    )
                    if len(resolved_call_sites) >= limit:
                        break
                if len(resolved_call_sites) >= limit:
                    break
            resolved_call_sites.sort(key=lambda item: item[0])
            call_sites = tuple(reference for _, reference in resolved_call_sites[:limit])
            if not call_sites and query == short_name:
                try:
                    raw_call_sites = self._reference_index.references_to(short_name, limit=limit)
                except (OSError, RuntimeError, TypeError, ValueError):
                    raw_call_sites = ()
                call_sites = self._call_references(raw_call_sites, limit=limit)
        elif query == short_name:
            # A raw reference index only knows the short token. Using it for a
            # qualified query would mix unrelated symbols that share that name.
            try:
                raw_call_sites = self._reference_index.references_to(short_name, limit=limit)
            except (OSError, RuntimeError, TypeError, ValueError):
                raw_call_sites = ()
            call_sites = self._call_references(raw_call_sites, limit=limit)
        else:
            call_sites = ()

        file_symbols: dict[str, tuple[SymbolRecord, ...]] = {}
        callers: list[tuple[tuple[str, int, int], SymbolCaller]] = []
        seen: set[tuple[str, int, int, str | None]] = set()
        for reference in call_sites:
            try:
                key = (
                    str(reference.path),
                    int(reference.line),
                    int(reference.column),
                    reference.scope,
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                continue
            if key in seen:
                continue
            seen.add(key)
            symbols = file_symbols.get(key[0])
            if symbols is None:
                try:
                    raw_symbols = self._symbol_index.symbols_for_file(key[0])
                except (OSError, RuntimeError, TypeError, ValueError):
                    raw_symbols = ()
                symbols = tuple(self._safe_iter(raw_symbols))
                file_symbols[key[0]] = symbols
            sort_key = (key[0].casefold(), key[1], key[2])
            callers.append((
                sort_key,
                SymbolCaller(reference, self._find_enclosing_symbol(reference, symbols)),
            ))
        callers.sort(key=lambda item: item[0])
        return tuple(item for _, item in callers[:limit])


    @staticmethod
    def _safe_iter(values: object) -> Iterator[object]:
        """Yield available records without letting a stale index abort a query."""
        try:
            iterator = iter(values)  # type: ignore[arg-type]
        except (OSError, RuntimeError, TypeError, ValueError):
            return
        while True:
            try:
                yield next(iterator)
            except StopIteration:
                return
            except (OSError, RuntimeError, TypeError, ValueError, UnicodeError, OverflowError, MemoryError, RecursionError):
                return


    @staticmethod
    def _safe_text(value: object, *, limit: int = 20_000) -> str:
        try:
            text = str(value).strip()
        except (TypeError, RuntimeError, ValueError, UnicodeError, RecursionError):
            return ""
        return text[:limit].replace("\x00", "")

    @staticmethod
    def _iter_resilient(values: object) -> Iterator[object]:
        """Backward-compatible resilient iterator used by stabilization tests."""
        yield from SymbolCallHierarchyService._safe_iter(values)

    @staticmethod
    def _call_references(
        records: object,
        *,
        limit: int,
    ) -> tuple[SymbolReferenceRecord, ...]:
        values: list[SymbolReferenceRecord] = []
        for item in SymbolCallHierarchyService._safe_iter(records):
            try:
                context = getattr(item, "context", None)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
            if context == "call":
                values.append(item)
                if len(values) >= limit:
                    break
        return tuple(values)

    @staticmethod
    def _find_enclosing_symbol(
        reference: SymbolReferenceRecord,
        symbols: tuple[SymbolRecord, ...],
    ) -> SymbolRecord | None:
        try:
            reference_line = int(reference.line)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return None

        containing: list[tuple[tuple[int, int, str], SymbolRecord]] = []
        for item in symbols:
            try:
                start_line = int(item.line)
                end_line = int(item.end_line)
                kind = str(item.kind)
                qualified_name = str(item.qualified_name)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                continue
            if (
                start_line <= reference_line <= end_line
                and kind in {"function", "async_function", "method", "async_method"}
            ):
                containing.append((
                    (end_line - start_line, -start_line, qualified_name),
                    item,
                ))
        if not containing:
            return None
        return min(containing, key=lambda candidate: candidate[0])[1]
