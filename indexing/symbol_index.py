"""Incremental project symbol index."""
from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Iterable

from .symbol_database import SymbolDatabase
from .symbol_parser import SymbolParseResult, SymbolParser, SymbolRecord


def _normalize_suffixes(suffixes: Iterable[str] | str) -> frozenset[str]:
    values = (suffixes,) if isinstance(suffixes, str) else tuple(suffixes)
    normalized: set[str] = set()
    for item in values:
        value = str(item).strip().casefold()
        if value:
            normalized.add(value if value.startswith(".") else f".{value}")
    if not normalized:
        raise ValueError("at least one non-empty suffix is required")
    return frozenset(normalized)


class SymbolIndex:
    def __init__(self, project_root: str | Path, *, suffixes: Iterable[str] = (".py", ".pyi")) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        if not self.root.is_dir():
            raise NotADirectoryError(str(self.root))
        self._suffixes = _normalize_suffixes(suffixes)
        self._parser = SymbolParser()
        self._database = SymbolDatabase(self.root)
        self._lock = RLock()

    @property
    def revision(self) -> int:
        return self._database.revision

    def _resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve(strict=False)

    def rebuild(self, paths: Iterable[str | Path] | str | Path | None = None) -> tuple[SymbolParseResult, ...]:
        full_rebuild = paths is None
        candidates = self._collect_candidates(paths)
        results: list[SymbolParseResult] = []
        staged: dict[Path, tuple[SymbolRecord, ...]] = {}
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
                staged[path] = result.symbols
            if full_rebuild:
                self._database.replace_all(staged)
            else:
                for path, symbols in staged.items():
                    self._database.replace_file(path, symbols)
        return tuple(sorted(results, key=lambda item: item.path.casefold()))

    def update_file(self, path: str | Path) -> SymbolParseResult | None:
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
                return SymbolParseResult(str(candidate), ())
            result = self._parser.parse_file(candidate)
            if result.parse_error is None:
                self._database.replace_file(candidate, result.symbols)
            return result

    def remove_file(self, path: str | Path) -> bool:
        candidate = self._resolve_path(path)
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return False
        with self._lock:
            return self._database.remove_file(candidate)

    def search(self, query: str, *, kinds: Iterable[str] = (), limit: int = 100) -> tuple[SymbolRecord, ...]:
        return self._database.search(query, kinds=kinds, limit=limit)

    def symbols_for_file(self, path: str | Path) -> tuple[SymbolRecord, ...]:
        return self._database.symbols_for_file(self._resolve_path(path))

    def stats(self) -> dict[str, int]:
        return self._database.stats()

    def integrity_check(self) -> bool:
        return self._database.integrity_check()

    def _collect_candidates(self, paths: Iterable[str | Path] | str | Path | None) -> tuple[Path, ...]:
        if paths is None:
            values = {path for suffix in self._suffixes for path in self.root.rglob(f"*{suffix}")}
        else:
            raw = (paths,) if isinstance(paths, (str, Path)) else tuple(paths)
            if any(not isinstance(path, (str, Path)) for path in raw):
                raise TypeError("paths must contain only str or Path values")
            values = {self._resolve_path(path) for path in raw}
        return tuple(sorted(values, key=lambda item: str(item).casefold()))
