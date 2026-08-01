"""Read-only symbol navigation facade for SAE code intelligence."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterable

from artmach_assistant.core.query_validation import bounded_positive_int, normalized_query

from artmach_assistant.indexing import (
    SymbolIndex,
    SymbolRecord,
    SymbolReferenceIndex,
    SymbolReferenceRecord,
    CrossFileReferenceResolver,
)


@dataclass(frozen=True, slots=True)
class SymbolNavigationResult:
    """Definitions and references collected for one symbol query."""

    query: str
    definitions: tuple[SymbolRecord, ...]
    references: tuple[SymbolReferenceRecord, ...]

    @property
    def found(self) -> bool:
        return bool(self.definitions or self.references)


class SymbolNavigationService:
    """Combines the definition and reference indexes without reparsing files."""

    def __init__(
        self,
        project_root: str | Path,
        symbol_index: SymbolIndex,
        reference_index: SymbolReferenceIndex,
        resolved_reference_index: CrossFileReferenceResolver | None = None,
    ) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._symbol_index = symbol_index
        self._reference_index = reference_index
        self._resolved_reference_index = resolved_reference_index
        self._lock = RLock()

    def locate(self, name: str, *, limit: int = 500) -> SymbolNavigationResult:
        query = normalized_query(name)
        if not query:
            return SymbolNavigationResult("", (), ())
        bounded = bounded_positive_int(limit, default=500, maximum=5000)
        with self._lock:
            reference_name = query.rsplit(".", 1)[-1]
            candidates = self._definition_candidates(query, reference_name, bounded)
            definitions = tuple(
                item for item in candidates if self._matches_definition(item, query)
            )[:bounded]
            canonical_names = self._canonical_names(query, definitions)
            if self._resolved_reference_index is not None:
                references = self._resolved_references(canonical_names, bounded)
                if not references and query == reference_name:
                    try:
                        raw_references = self._reference_index.references_to(
                            reference_name, limit=bounded
                        )
                    except (OSError, RuntimeError, TypeError, ValueError):
                        raw_references = ()
                    references = self._unique_references(raw_references)[:bounded]
            elif query == reference_name:
                # The raw reference index is keyed by the short token. It is only
                # a safe fallback for an unqualified query; otherwise unrelated
                # symbols sharing the same final component would be merged.
                try:
                    raw_references = self._reference_index.references_to(
                        reference_name, limit=bounded
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    raw_references = ()
                references = self._unique_references(raw_references)[:bounded]
            else:
                references = ()
        return SymbolNavigationResult(query, definitions, references)

    def workspace_search(self, query: str, *, limit: int = 100) -> tuple[SymbolRecord, ...]:
        """Return workspace-wide symbol matches in deterministic relevance order."""
        normalized = normalized_query(query)
        if not normalized:
            return ()
        bounded = bounded_positive_int(limit, default=100, maximum=5000)
        short_name = normalized.rsplit(".", 1)[-1]
        candidate_limit = max(1000, bounded)
        with self._lock:
            candidates: list[SymbolRecord] = []
            # Query both forms: qualified queries need namespace-aware matches,
            # while the short token preserves fuzzy discovery in indexes keyed by
            # the declaration name.
            for value in dict.fromkeys((normalized, short_name)):
                try:
                    candidates.extend(self._symbol_index.search(value, limit=candidate_limit))
                except (OSError, RuntimeError, TypeError, ValueError):
                    continue

            unique: dict[tuple[str, int, int, str], SymbolRecord] = {}
            for item in self._safe_iter(candidates):
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

            ranked = sorted(
                unique.values(),
                key=lambda item: self._workspace_rank(item, normalized),
            )
            return tuple(ranked[:bounded])

    def definitions(self, name: str, *, limit: int = 100) -> tuple[SymbolRecord, ...]:
        return self.locate(name, limit=limit).definitions

    def references(self, name: str, *, limit: int = 500) -> tuple[SymbolReferenceRecord, ...]:
        return self.locate(name, limit=limit).references

    def _definition_candidates(
        self, query: str, short_name: str, limit: int
    ) -> tuple[SymbolRecord, ...]:
        """Collect exact and short-name candidates before applying result limits."""
        candidate_limit = max(1000, limit)
        unique: dict[tuple[str, int, int, str], SymbolRecord] = {}
        for value in dict.fromkeys((query, short_name)):
            try:
                candidates = self._symbol_index.search(value, limit=candidate_limit)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            try:
                iterator = iter(candidates)
            except TypeError:
                continue
            for item in self._safe_iter(iterator):
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
        return tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    str(item.path).casefold(),
                    item.line,
                    item.column,
                    item.qualified_name.casefold(),
                ),
            )
        )

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
            if not value:
                continue
            unique.setdefault(value.casefold(), value)
        return tuple(unique.values())

    def _resolved_references(
        self, canonical_names: tuple[str, ...], limit: int
    ) -> tuple[SymbolReferenceRecord, ...]:
        if self._resolved_reference_index is None:
            return ()
        values: list[SymbolReferenceRecord] = []
        for canonical_name in canonical_names:
            try:
                bindings = self._resolved_reference_index.bindings_to(
                    canonical_name, limit=limit
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            try:
                iterator = iter(bindings)
            except TypeError:
                continue
            for item in self._safe_iter(iterator):
                reference = getattr(item, "reference", None)
                if reference is not None:
                    values.append(reference)
        return self._unique_references(values)[:limit]

    @staticmethod
    def _unique_references(
        items: Iterable[SymbolReferenceRecord],
    ) -> tuple[SymbolReferenceRecord, ...]:
        unique: dict[tuple[str, int, int, str, str, str | None], SymbolReferenceRecord] = {}
        try:
            iterator = iter(items)
        except TypeError:
            return ()
        for item in SymbolNavigationService._safe_iter(iterator):
            try:
                key = (
                    str(item.path).casefold(),
                    int(item.line),
                    int(item.column),
                    str(item.name).casefold(),
                    str(item.context).casefold(),
                    str(item.scope).casefold() if item.scope is not None else None,
                )
            except (AttributeError, TypeError, ValueError):
                continue
            unique.setdefault(key, item)
        return tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    str(item.path).casefold(),
                    item.line,
                    item.column,
                    item.name.casefold(),
                ),
            )
        )

    def _workspace_rank(self, item: SymbolRecord, query: str) -> tuple[object, ...]:
        name = item.name.casefold()
        qualified = item.qualified_name.casefold()
        canonical = self._canonical_name(item).casefold()
        needle = query.casefold()
        short = needle.rsplit(".", 1)[-1]

        if canonical == needle:
            relevance = 0
        elif qualified == needle:
            relevance = 1
        elif name == needle or name == short:
            relevance = 2
        elif canonical.endswith(f".{needle}") or qualified.endswith(f".{needle}"):
            relevance = 3
        elif name.startswith(short) or qualified.startswith(needle):
            relevance = 4
        elif short in name or needle in qualified or needle in canonical:
            relevance = 5
        else:
            relevance = 6
        return (
            relevance,
            len(canonical) if canonical else len(qualified),
            canonical or qualified,
            str(item.path).casefold(),
            item.line,
            item.column,
        )

    def _matches_definition(self, item: SymbolRecord, query: str) -> bool:
        try:
            needle = query.casefold()
            name = str(item.name).casefold()
            qualified = str(item.qualified_name).casefold()
        except (AttributeError, TypeError, ValueError):
            return False
        if name == needle or qualified == needle:
            return True
        return self._canonical_name(item).casefold() == needle

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
        if value is None:
            return ""
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
