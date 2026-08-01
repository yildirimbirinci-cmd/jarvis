"""Thread-safe project-wide registry for indexed Python symbols."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from .symbol_parser import SymbolRecord
from .stats_mapping import RevisionStats


@dataclass(frozen=True, slots=True)
class ProjectSymbol:
    name: str
    qualified_name: str
    canonical_name: str
    module: str
    kind: str
    path: str
    line: int
    end_line: int
    column: int
    parent: str | None = None
    bases: tuple[str, ...] = ()
    signature: str = ""

    @classmethod
    def from_record(cls, root: Path, record: SymbolRecord) -> "ProjectSymbol":
        path = Path(record.path).expanduser().resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"symbol path is outside project root: {path}") from exc
        module = _module_name(root, path)
        qualified = record.qualified_name.strip() or record.name.strip()
        canonical = f"{module}.{qualified}" if module else qualified
        return cls(
            record.name,
            qualified,
            canonical,
            module,
            record.kind,
            str(path),
            record.line,
            record.end_line,
            record.column,
            record.parent,
            tuple(record.bases),
            record.signature,
        )


class ProjectSymbolRegistry:
    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        if not self.root.is_dir():
            raise NotADirectoryError(str(self.root))
        self._by_file: dict[str, tuple[ProjectSymbol, ...]] = {}
        self._by_name: dict[str, tuple[ProjectSymbol, ...]] = {}
        self._by_qualified: dict[str, tuple[ProjectSymbol, ...]] = {}
        self._by_canonical: dict[str, tuple[ProjectSymbol, ...]] = {}
        self._by_module: dict[str, tuple[ProjectSymbol, ...]] = {}
        self._revision = 0
        self._lock = RLock()

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def replace_file(self, path: str | Path, symbols: Iterable[SymbolRecord]) -> bool:
        absolute = str(self._resolve_path(path))
        converted = self._materialize_symbols(symbols, expected_path=Path(absolute))
        with self._lock:
            if self._by_file.get(absolute) == converted:
                return False
            self._by_file[absolute] = converted
            self._revision += 1
            self._rebuild_lookups()
            return True

    def remove_file(self, path: str | Path) -> bool:
        absolute = str(self._resolve_path(path))
        with self._lock:
            if absolute not in self._by_file:
                return False
            del self._by_file[absolute]
            self._revision += 1
            self._rebuild_lookups()
            return True

    def clear(self) -> bool:
        with self._lock:
            if not self._by_file:
                return False
            self._by_file.clear()
            self._by_name.clear()
            self._by_qualified.clear()
            self._by_canonical.clear()
            self._by_module.clear()
            self._revision += 1
            return True

    def exact(self, query: str, *, limit: int = 100) -> tuple[ProjectSymbol, ...]:
        if not isinstance(query, str):
            return ()
        text = query.strip().casefold()
        if not text:
            return ()
        with self._lock:
            values = (
                self._by_canonical.get(text)
                or self._by_qualified.get(text)
                or self._by_name.get(text)
                or ()
            )
            return values[:_safe_limit(limit)]

    def canonical(self, name: str, *, limit: int = 100) -> tuple[ProjectSymbol, ...]:
        text = name.strip().casefold() if isinstance(name, str) else ""
        if not text:
            return ()
        with self._lock:
            return self._by_canonical.get(text, ())[:_safe_limit(limit)]

    def module_symbols(self, module: str) -> tuple[ProjectSymbol, ...]:
        if not isinstance(module, str):
            return ()
        text = module.strip().casefold()
        if not text:
            return ()
        with self._lock:
            return self._by_module.get(text, ())

    def search(self, query: str, *, limit: int = 100) -> tuple[ProjectSymbol, ...]:
        if not isinstance(query, str):
            return ()
        text = query.strip().casefold()
        if not text:
            return ()
        with self._lock:
            values = tuple(item for group in self._by_file.values() for item in group)
        matches = [
            item
            for item in values
            if text in item.name.casefold()
            or text in item.qualified_name.casefold()
            or text in item.canonical_name.casefold()
        ]
        matches.sort(key=lambda item: _sort_key(item, text))
        return tuple(matches[:_safe_limit(limit)])

    def symbols_for_file(self, path: str | Path) -> tuple[ProjectSymbol, ...]:
        absolute = str(self._resolve_path(path))
        with self._lock:
            return self._by_file.get(absolute, ())

    def all_symbols(self) -> tuple[ProjectSymbol, ...]:
        with self._lock:
            return tuple(item for path in sorted(self._by_file) for item in self._by_file[path])

    def snapshot(self) -> dict[str, Any]:
        """Return a deterministic JSON-serializable state snapshot."""
        with self._lock:
            return {
                "revision": self._revision,
                "files": {
                    path: [_snapshot_symbol(symbol) for symbol in symbols]
                    for path, symbols in sorted(self._by_file.items(), key=lambda item: item[0].casefold())
                },
            }

    def stats(self) -> dict[str, int]:
        with self._lock:
            return RevisionStats({
                "project_symbols": sum(map(len, self._by_file.values())),
                "project_symbol_files": len(self._by_file),
                "project_symbol_revision": self._revision,
            })

    def _resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path is outside project root: {resolved}") from exc
        return resolved

    def _materialize_symbols(
        self,
        symbols: Iterable[SymbolRecord],
        *,
        expected_path: Path,
    ) -> tuple[ProjectSymbol, ...]:
        if symbols is None or isinstance(symbols, (str, bytes, bytearray, memoryview, Path)):
            raise TypeError("symbols must be an iterable of SymbolRecord values")
        try:
            records = tuple(symbols)
        except (TypeError, ValueError, RuntimeError, OverflowError, MemoryError, RecursionError) as exc:
            raise ValueError("symbols iterable failed") from exc
        if any(not isinstance(item, SymbolRecord) for item in records):
            raise TypeError("symbols must contain only SymbolRecord values")
        converted = tuple(ProjectSymbol.from_record(self.root, item) for item in records)
        if any(Path(item.path) != expected_path for item in converted):
            raise ValueError("symbol record path does not match replaced file")
        return tuple(dict.fromkeys(converted))

    def _rebuild_lookups(self) -> None:
        names: dict[str, list[ProjectSymbol]] = {}
        qualified: dict[str, list[ProjectSymbol]] = {}
        canonical: dict[str, list[ProjectSymbol]] = {}
        modules: dict[str, list[ProjectSymbol]] = {}
        for symbols in self._by_file.values():
            for item in symbols:
                names.setdefault(item.name.casefold(), []).append(item)
                qualified.setdefault(item.qualified_name.casefold(), []).append(item)
                canonical.setdefault(item.canonical_name.casefold(), []).append(item)
                modules.setdefault(item.module.casefold(), []).append(item)
        self._by_name = _freeze(names)
        self._by_qualified = _freeze(qualified)
        self._by_canonical = _freeze(canonical)
        self._by_module = _freeze(modules)


def _snapshot_symbol(symbol: ProjectSymbol) -> dict[str, Any]:
    payload = asdict(symbol)
    payload["bases"] = list(symbol.bases)
    return payload


def _freeze(source: dict[str, list[ProjectSymbol]]) -> dict[str, tuple[ProjectSymbol, ...]]:
    return {key: tuple(sorted(value, key=_stable_key)) for key, value in source.items()}


def _module_name(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return ""
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _stable_key(item: ProjectSymbol) -> tuple[str, int, int, str]:
    return (item.path.casefold(), item.line, item.column, item.canonical_name.casefold())


def _sort_key(item: ProjectSymbol, query: str) -> tuple[int, int, str, int, int]:
    name = item.name.casefold()
    qualified = item.qualified_name.casefold()
    canonical = item.canonical_name.casefold()
    rank = 0 if canonical == query else 1 if qualified == query else 2 if name == query else 3
    return (rank, len(canonical), item.path.casefold(), item.line, item.column)


def _safe_limit(value: object, *, default: int = 100) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(number):
        return default
    return max(1, min(int(number), 10_000))
