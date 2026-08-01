"""Incremental project symbol-reference index."""
from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Iterable

from .symbol_reference_database import SymbolReferenceDatabase
from .symbol_reference_parser import SymbolReferenceParseResult, SymbolReferenceParser, SymbolReferenceRecord


class SymbolReferenceIndex:
    def __init__(self, project_root: str | Path, *, suffixes: Iterable[str] = (".py", ".pyi")) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        if not self.root.is_dir():
            raise NotADirectoryError(str(self.root))
        self._suffixes = _normalize_suffixes(suffixes)
        self._parser = SymbolReferenceParser()
        self._database = SymbolReferenceDatabase(self.root)
        self._lock = RLock()

    @property
    def revision(self) -> int:
        return self._database.revision

    def _resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve(strict=False)

    def rebuild(self, paths: Iterable[str | Path] | str | Path | None = None) -> tuple[SymbolReferenceParseResult, ...]:
        full_rebuild = paths is None
        candidates = self._collect_candidates(paths)
        results: list[SymbolReferenceParseResult] = []
        staged: dict[Path, tuple[SymbolReferenceRecord, ...]] = {}
        with self._lock:
            for path in candidates:
                if not path.is_file():
                    if not full_rebuild:
                        self._database.remove_file(path)
                    continue
                result = self._parser.parse_file(path)
                results.append(result)
                if result.parse_error is not None:
                    if full_rebuild:
                        return tuple(sorted(results, key=lambda item: item.path.casefold()))
                    continue
                staged[path] = result.references
            if full_rebuild:
                self._database.replace_all(staged)
            else:
                for path, references in staged.items():
                    self._database.replace_file(path, references)
        return tuple(sorted(results, key=lambda item: item.path.casefold()))

    def update_file(self, path: str | Path) -> SymbolReferenceParseResult | None:
        candidate = self._resolve_path(path)
        if candidate.suffix.casefold() not in self._suffixes:
            return None
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        with self._lock:
            if not candidate.is_file():
                self._database.remove_file(candidate)
                return SymbolReferenceParseResult(str(candidate), ())
            result = self._parser.parse_file(candidate)
            if result.parse_error is None:
                self._database.replace_file(candidate, result.references)
            return result

    def remove_file(self, path: str | Path) -> bool:
        candidate = self._resolve_path(path)
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return False
        with self._lock:
            return self._database.remove_file(candidate)

    def references_to(self, name: str, *, limit: int = 500) -> tuple[SymbolReferenceRecord, ...]:
        return self._database.references_to(name, limit=limit)

    def references_for_file(self, path: str | Path) -> tuple[SymbolReferenceRecord, ...]:
        if not isinstance(path, (str, Path)):
            return ()
        candidate = self._resolve_path(path)
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return ()
        return self._database.references_for_file(candidate)

    def stats(self) -> dict[str, int]:
        return self._database.stats()

    def integrity_check(self) -> bool:
        return self._database.integrity_check()

    def _collect_candidates(self, paths: Iterable[str | Path] | str | Path | None) -> tuple[Path, ...]:
        if paths is None:
            values = {path for suffix in self._suffixes for path in self.root.rglob(f"*{suffix}")}
        else:
            try:
                raw = (paths,) if isinstance(paths, (str, Path)) else tuple(paths)
            except (
                TypeError,
                ValueError,
                RuntimeError,
                OverflowError,
                MemoryError,
                RecursionError,
            ) as exc:
                raise ValueError("unable to materialize paths") from exc
            if any(not isinstance(path, (str, Path)) for path in raw):
                raise TypeError("paths must contain only str or Path values")
            values = {self._resolve_path(path) for path in raw}
        return tuple(sorted(values, key=lambda item: str(item).casefold()))


def _normalize_suffixes(suffixes: Iterable[str] | str) -> frozenset[str]:
    values = (suffixes,) if isinstance(suffixes, str) else tuple(suffixes)
    normalized = frozenset(
        (value if value.startswith(".") else f".{value}").casefold()
        for value in values if isinstance(value, str) and value.strip()
    )
    if not normalized:
        raise ValueError("suffixes must contain at least one non-empty string")
    return normalized
