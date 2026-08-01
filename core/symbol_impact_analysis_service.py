"""Read-only symbol impact analysis built on SAE definition and reference indexes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING
from pathlib import Path
from threading import RLock

from artmach_assistant.core.query_validation import bounded_positive_int, normalized_query

from artmach_assistant.indexing import (
    SymbolIndex,
    SymbolRecord,
    SymbolReferenceIndex,
    SymbolReferenceRecord,
    CrossFileReferenceResolver,
)

if TYPE_CHECKING:
    from artmach_assistant.indexing.call_graph import CallGraph, CallGraphEdge
else:
    CallGraph = Any
    CallGraphEdge = Any


@dataclass(frozen=True, slots=True)
class SymbolImpactFile:
    """One workspace file affected by a symbol and the evidence for that impact."""

    path: str
    definitions: tuple[SymbolRecord, ...] = ()
    references: tuple[SymbolReferenceRecord, ...] = ()
    call_edges: tuple[CallGraphEdge, ...] = ()
    call_count: int = 0
    read_count: int = 0

    @property
    def weight(self) -> int:
        """Stable relative impact score; it does not execute or inspect runtime code."""
        return (len(self.definitions) * 5) + (self.call_count * 3) + self.read_count


@dataclass(frozen=True, slots=True)
class SymbolImpactResult:
    """Definition and file-level impact summary for one symbol query."""

    query: str
    definitions: tuple[SymbolRecord, ...]
    files: tuple[SymbolImpactFile, ...]
    unresolved_reference_count: int = 0

    @property
    def found(self) -> bool:
        return bool(self.definitions or self.files)

    @property
    def impacted_file_count(self) -> int:
        return len(self.files)

    @property
    def reference_count(self) -> int:
        return sum(len(item.references) for item in self.files)


class SymbolImpactAnalysisService:
    """Groups symbol definitions and references into a deterministic impact view.

    Only persistent indexes are queried. Workspace modules are never imported or
    executed, so this service is safe to use from the UI, voice layer and patch
    planning code.
    """

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

    def analyze(self, name: str, *, limit: int = 2000) -> SymbolImpactResult:
        query = normalized_query(name)
        if not query:
            return SymbolImpactResult("", (), ())

        bounded = bounded_positive_int(limit, default=2000, maximum=10000)
        short_name = query.rsplit(".", 1)[-1]
        with self._lock:
            definitions = self._definitions(query, short_name, bounded)
            canonical_names = self._canonical_names(query, definitions)
            if self._resolved_reference_index is not None:
                references = self._resolved_references(canonical_names, bounded)
                if not references and query == short_name:
                    references = self._unresolved_references(short_name, bounded)
            elif query == short_name:
                # The unresolved reference index is short-name based. It is a
                # safe fallback only for an unqualified query; otherwise symbols
                # with the same final component are incorrectly merged.
                references = self._unresolved_references(short_name, bounded)
            else:
                references = ()
            call_edges = self._incoming_call_edges(canonical_names, bounded)

        grouped_definitions: dict[str, list[SymbolRecord]] = {}
        for definition in definitions:
            grouped_definitions.setdefault(definition.path, []).append(definition)

        grouped_references: dict[str, list[SymbolReferenceRecord]] = {}
        for reference in references:
            grouped_references.setdefault(reference.path, []).append(reference)

        grouped_call_edges: dict[str, list[CallGraphEdge]] = {}
        for edge in call_edges:
            grouped_call_edges.setdefault(edge.caller_path, []).append(edge)

        paths = set(grouped_definitions) | set(grouped_references) | set(grouped_call_edges)
        files: list[SymbolImpactFile] = []
        for path in paths:
            file_definitions = tuple(
                sorted(
                    grouped_definitions.get(path, ()),
                    key=lambda item: (item.line, item.column, item.qualified_name),
                )
            )
            file_references = tuple(
                sorted(
                    grouped_references.get(path, ()),
                    key=lambda item: (item.line, item.column, item.context),
                )
            )
            file_call_edges = tuple(
                sorted(
                    grouped_call_edges.get(path, ()),
                    key=lambda item: (
                        item.call_line,
                        item.call_column,
                        item.callee_canonical_name.casefold(),
                    ),
                )
            )
            reference_calls = {
                (item.path, item.line, item.column)
                for item in file_references
                if item.context == "call"
            }
            graph_calls = {
                (item.caller_path, item.call_line, item.call_column)
                for item in file_call_edges
            }
            files.append(
                SymbolImpactFile(
                    path=path,
                    definitions=file_definitions,
                    references=file_references,
                    call_edges=file_call_edges,
                    call_count=len(reference_calls | graph_calls),
                    read_count=sum(1 for item in file_references if item.context != "call"),
                )
            )

        ordered = tuple(
            sorted(
                files,
                key=lambda item: (-item.weight, item.path.casefold()),
            )
        )
        unresolved = len(references) if not definitions and not call_edges else 0
        return SymbolImpactResult(query, definitions, ordered, unresolved)


    def _definitions(
        self, query: str, short_name: str, limit: int
    ) -> tuple[SymbolRecord, ...]:
        candidate_limit = max(1000, limit)
        unique: dict[tuple[str, int, int, str], SymbolRecord] = {}

        def collect(value: str) -> int:
            try:
                candidates = tuple(self._safe_iter(self._symbol_index.search(value, limit=candidate_limit)))
            except (OSError, RuntimeError, TypeError, ValueError):
                candidates = ()
            for item in candidates:
                try:
                    key = (
                        str(item.path).casefold(),
                        int(item.line),
                        int(item.column),
                        str(item.qualified_name).casefold(),
                    )
                except (AttributeError, TypeError, ValueError):
                    continue
                unique.setdefault(key, item)
            return len(candidates)

        primary_count = collect(query)
        exact_after_primary = any(
            item.name == query
            or item.qualified_name == query
            or self._canonical_name(item) == query
            for item in unique.values()
        )
        if query != short_name and (not exact_after_primary or primary_count <= limit):
            collect(short_name)

        matches = (
            item
            for item in unique.values()
            if item.name == query
            or item.qualified_name == query
            or self._canonical_name(item) == query
        )
        return tuple(
            sorted(
                matches,
                key=lambda item: (
                    str(item.path).casefold(),
                    item.line,
                    item.column,
                    item.qualified_name.casefold(),
                ),
            )
        )[:limit]

    def _resolved_references(
        self, canonical_names: tuple[str, ...], limit: int
    ) -> tuple[SymbolReferenceRecord, ...]:
        if self._resolved_reference_index is None:
            return ()
        unique: dict[
            tuple[str, int, int, str, str], SymbolReferenceRecord
        ] = {}
        for canonical_name in canonical_names:
            try:
                bindings = self._resolved_reference_index.bindings_to(
                    canonical_name, limit=limit
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            try:
                iterator = iter(bindings)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            for binding in self._safe_iter(iterator):
                reference = getattr(binding, "reference", None)
                if reference is None:
                    continue
                try:
                    key = (
                        str(reference.path).casefold(),
                        int(reference.line),
                        int(reference.column),
                        str(reference.context).casefold(),
                        str(reference.scope or "").casefold(),
                    )
                except (AttributeError, TypeError, ValueError):
                    continue
                unique.setdefault(key, reference)
        ordered = sorted(
            unique.values(),
            key=lambda item: (
                str(item.path).casefold(),
                int(item.line),
                int(item.column),
                str(item.context).casefold(),
                str(item.scope or "").casefold(),
            ),
        )
        return tuple(ordered[:limit])

    def _unresolved_references(
        self, short_name: str, limit: int
    ) -> tuple[SymbolReferenceRecord, ...]:
        try:
            values = self._reference_index.references_to(short_name, limit=limit)
        except (OSError, RuntimeError, TypeError, ValueError):
            return ()
        unique: dict[
            tuple[str, int, int, str, str], SymbolReferenceRecord
        ] = {}
        try:
            iterator = iter(values)
        except (OSError, RuntimeError, TypeError, ValueError):
            return ()
        for item in self._safe_iter(iterator):
            try:
                key = (
                    str(item.path).casefold(),
                    int(item.line),
                    int(item.column),
                    str(item.context).casefold(),
                    str(item.scope or "").casefold(),
                )
            except (AttributeError, TypeError, ValueError):
                continue
            unique.setdefault(key, item)
        ordered = sorted(
            unique.values(),
            key=lambda item: (
                str(item.path).casefold(),
                int(item.line),
                int(item.column),
                str(item.context).casefold(),
                str(item.scope or "").casefold(),
            ),
        )
        return tuple(ordered[:limit])

    def _canonical_names(
        self, query: str, definitions: tuple[SymbolRecord, ...]
    ) -> tuple[str, ...]:
        values: list[str] = [query]
        for item in definitions:
            canonical = self._canonical_name(item)
            if canonical:
                values.append(canonical)
        unique: dict[str, str] = {}
        for value in values:
            if value:
                unique.setdefault(value.casefold(), value)
        return tuple(unique.values())


    def _canonical_name(self, item: SymbolRecord) -> str:
        try:
            raw_path = self._safe_text(getattr(item, "path", ""))
            qualified_name = self._safe_text(getattr(item, "qualified_name", ""))
            if not raw_path or not qualified_name:
                return ""
            path = Path(raw_path).expanduser()
            absolute = path.resolve(strict=False) if path.is_absolute() else (self.root / path).resolve(strict=False)
            relative = absolute.relative_to(self.root)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return ""
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        module = ".".join(parts)
        return f"{module}.{qualified_name}" if module else qualified_name

    @staticmethod
    def _safe_text(value: object, *, limit: int = 20_000) -> str:
        try:
            text = str(value).strip()
        except (TypeError, RuntimeError, ValueError, UnicodeError, RecursionError):
            return ""
        return text[:limit].replace("\x00", "")

    @staticmethod
    def _safe_iter(values: object):
        try:
            iterator = iter(values)
        except (OSError, RuntimeError, TypeError, ValueError):
            return
        while True:
            try:
                yield next(iterator)
            except StopIteration:
                return
            except (OSError, RuntimeError, TypeError, ValueError, UnicodeError, OverflowError, MemoryError, RecursionError):
                return

    def _incoming_call_edges(
        self, canonical_names: tuple[str, ...], limit: int
    ) -> tuple[CallGraphEdge, ...]:
        if self._call_graph is None:
            return ()
        unique: dict[tuple[str, int, int, str, str], CallGraphEdge] = {}
        for canonical_name in canonical_names:
            try:
                edges = self._call_graph.callers(canonical_name, limit=limit)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            try:
                iterator = iter(edges)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            for edge in self._safe_iter(iterator):
                try:
                    key = (
                        str(edge.caller_path).casefold(),
                        int(edge.call_line),
                        int(edge.call_column),
                        str(edge.callee_canonical_name).casefold(),
                        str(edge.callee_path).casefold(),
                    )
                except (AttributeError, TypeError, ValueError):
                    continue
                unique.setdefault(key, edge)
        ordered = sorted(
            unique.values(),
            key=lambda edge: (
                str(edge.caller_path).casefold(),
                int(edge.call_line),
                int(edge.call_column),
                str(edge.callee_canonical_name).casefold(),
                str(edge.callee_path).casefold(),
            ),
        )
        return tuple(ordered[:limit])

