"""Conservative dead-code candidates built on indexed symbol navigation and AST usage."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Iterable, Iterator

from artmach_assistant.core.query_validation import bounded_positive_int

if TYPE_CHECKING:
    from artmach_assistant.core.symbol_navigation_service import SymbolNavigationService

_MAX_SOURCE_BYTES = 4_000_000
_MAX_FILES = 20_000


@dataclass(frozen=True, slots=True)
class DeadCodeCandidate:
    path: str
    line: int
    column: int
    name: str
    kind: str
    reason: str
    confidence: str = "medium"


@dataclass(frozen=True, slots=True)
class DeadCodeReport:
    root: str
    candidates: tuple[DeadCodeCandidate, ...]
    scanned_files: int
    parse_errors: int = 0

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


class DeadCodeDetector:
    """Find conservative unused-code candidates without importing project modules."""

    def __init__(self, project_root: str | Path, navigation: "SymbolNavigationService") -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self._navigation = navigation
        self._lock = RLock()

    def analyze(
        self,
        paths: Iterable[str | Path] | None = None,
        *,
        limit: int = 2000,
    ) -> DeadCodeReport:
        bounded = bounded_positive_int(limit, default=2000, maximum=10000)
        files = self._source_files(paths)
        candidates: list[DeadCodeCandidate] = []
        parse_errors = 0
        scanned = 0
        with self._lock:
            for path in files:
                if len(candidates) >= bounded:
                    break
                source = self._read_source(path)
                if source is None:
                    parse_errors += 1
                    continue
                try:
                    tree = ast.parse(source, filename=str(path))
                except (SyntaxError, ValueError):
                    parse_errors += 1
                    continue
                scanned += 1
                relative = self._relative(path)
                candidates.extend(self._unused_imports(tree, relative))
                if len(candidates) >= bounded:
                    break
                for node in tree.body:
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue
                    if not self._eligible_definition(node):
                        continue
                    if self._has_external_reference(node.name, path, int(node.lineno)):
                        continue
                    candidates.append(
                        DeadCodeCandidate(
                            path=relative,
                            line=int(node.lineno),
                            column=int(node.col_offset),
                            name=node.name,
                            kind="class" if isinstance(node, ast.ClassDef) else "function",
                            reason="Özel üst-seviye sembol için indekslenmiş kullanım bulunamadı.",
                            confidence="medium",
                        )
                    )
                    if len(candidates) >= bounded:
                        break
        ordered = tuple(
            sorted(
                candidates[:bounded],
                key=lambda item: (item.path.casefold(), item.line, item.column, item.name.casefold()),
            )
        )
        return DeadCodeReport(str(self.root), ordered, scanned, parse_errors)

    def _source_files(self, paths: Iterable[str | Path] | None) -> tuple[Path, ...]:
        values: Iterator[object]
        if paths is None:
            try:
                values = iter(self.root.rglob("*.py"))
            except (OSError, RuntimeError):
                return ()
        else:
            try:
                values = iter(paths)
            except TypeError:
                return ()
        result: list[Path] = []
        for raw in values:
            if len(result) >= _MAX_FILES:
                break
            try:
                value = Path(raw).expanduser()
                absolute = value if value.is_absolute() else self.root / value
                absolute = absolute.resolve(strict=False)
                if absolute.is_symlink() or not absolute.is_file():
                    continue
                if absolute.suffix.casefold() not in {".py", ".pyi"}:
                    continue
                absolute.relative_to(self.root)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
            result.append(absolute)
        return tuple(sorted(set(result), key=lambda item: str(item).casefold()))

    @staticmethod
    def _eligible_definition(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
        if node.name in {"__main__", "main"}:
            return False
        if not node.name.startswith("_") or node.name.startswith("__"):
            return False
        decorators = {
            decorator.id
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Name)
        }
        return not decorators.intersection({"property", "staticmethod", "classmethod"})

    def _has_external_reference(self, name: str, path: Path, line: int) -> bool:
        try:
            result = self._navigation.locate(name, limit=5000)
            references = getattr(result, "references", ())
            iterator = iter(references)
        except (Exception, KeyboardInterrupt):
            return True
        try:
            for item in iterator:
                try:
                    item_path = getattr(item, "path")
                    item_line = int(getattr(item, "line"))
                except (AttributeError, TypeError, ValueError, OverflowError):
                    continue
                if not (self._same_path(item_path, path) and item_line == line):
                    return True
        except (Exception, KeyboardInterrupt):
            return True
        return False

    @staticmethod
    def _read_source(path: Path) -> str | None:
        try:
            size = path.stat().st_size
            if size < 0 or size > _MAX_SOURCE_BYTES:
                return None
            with path.open("r", encoding="utf-8", errors="strict") as handle:
                text = handle.read(_MAX_SOURCE_BYTES + 1)
            return text if len(text.encode("utf-8")) <= _MAX_SOURCE_BYTES else None
        except (OSError, RuntimeError, UnicodeError):
            return None

    def _unused_imports(self, tree: ast.Module, relative: str) -> tuple[DeadCodeCandidate, ...]:
        imported: list[tuple[str, int, int]] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.append((alias.asname or alias.name.split(".", 1)[0], node.lineno, node.col_offset))
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    imported.append((alias.asname or alias.name, node.lineno, node.col_offset))
        used = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        return tuple(
            DeadCodeCandidate(
                path=relative,
                line=int(line),
                column=int(column),
                name=name,
                kind="import",
                reason="İçe aktarılan ad bu dosyada okunmuyor.",
                confidence="high",
            )
            for name, line, column in imported
            if name not in used
        )

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _same_path(left: str | Path, right: Path) -> bool:
        try:
            return Path(left).expanduser().resolve(strict=False) == right
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
