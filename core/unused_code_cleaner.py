"""Staged, conservative removal of unused Python code."""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.dead_code_detector import DeadCodeCandidate, DeadCodeDetector
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator, RefactoringKind, RefactoringPlan
from artmach_assistant.core.safe_import_optimizer import SafeImportOptimizer
from artmach_assistant.core.workspace import WorkspaceError

_MAX_LIMIT = 10_000
_MAX_FILES = 8
_MAX_SOURCE_CHARS = 2_000_000


@dataclass(frozen=True, slots=True)
class UnusedCodeCleanupResult:
    path: str
    content: str
    removed_symbols: tuple[str, ...]
    removed_imports: tuple[str, ...]
    removed_duplicate_imports: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.removed_symbols or self.removed_imports or self.removed_duplicate_imports)


class UnusedCodeCleaner:
    """Prepare user-approved cleanups for high-confidence unused code."""

    def __init__(self, coordinator: RefactoringCoordinator, detector: DeadCodeDetector) -> None:
        self._coordinator = coordinator
        self._detector = detector
        self._imports = SafeImportOptimizer(coordinator)

    def analyze(self, paths: Iterable[str | Path] | None = None, *, include_imports: bool = True, limit: int = 200) -> tuple[UnusedCodeCleanupResult, ...]:
        if type(include_imports) is not bool:
            raise WorkspaceError("include_imports yalnızca boolean olabilir.")
        if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
            raise WorkspaceError(f"Temizlik limiti 1 ile {_MAX_LIMIT} arasında bir tam sayı olmalıdır.")
        report = self._detector.analyze(paths, limit=limit)
        try:
            candidates_iter = iter(report.candidates)
        except BaseException as exc:
            raise WorkspaceError("Ölü kod raporu geçersiz.") from exc
        grouped: dict[str, list[DeadCodeCandidate]] = {}
        try:
            for candidate in candidates_iter:
                path = str(candidate.path)
                if not path or "\x00" in path:
                    continue
                grouped.setdefault(path, []).append(candidate)
        except BaseException as exc:
            raise WorkspaceError("Ölü kod adayları okunamadı.") from exc
        if len(grouped) > _MAX_FILES:
            raise WorkspaceError(f"Temizlik {_MAX_FILES} dosyadan fazlasını etkiliyor; daha dar bir dosya grubu seç.")

        results: list[UnusedCodeCleanupResult] = []
        workspace = self._coordinator._editor.workspace
        for path in sorted(grouped, key=str.casefold):
            source = workspace.read_text(path, max_chars=_MAX_SOURCE_CHARS + 1)
            if not isinstance(source, str) or len(source) > _MAX_SOURCE_CHARS or "\x00" in source:
                raise WorkspaceError(f"Kaynak dosya analiz için geçersiz veya çok büyük: {path}")
            tree = self._parse(source, path)
            lines = source.splitlines(keepends=True)
            replacements: list[tuple[int, int, str]] = []
            removed_symbols: list[str] = []
            seen_defs: set[tuple[int, int]] = set()
            for candidate in grouped[path]:
                if candidate.kind not in {"function", "class"}:
                    continue
                node = self._matching_definition(tree, candidate)
                if node is None:
                    raise WorkspaceError(f"Ölü kod adayı güncel dosyayla eşleşmiyor: {path}:{candidate.line} {candidate.name}")
                start = self._decorated_start(node) - 1
                end = int(getattr(node, "end_lineno", node.lineno))
                marker = (start, end)
                if marker in seen_defs:
                    continue
                if any(not (end <= old_start or start >= old_end) for old_start, old_end, _ in replacements):
                    raise WorkspaceError(f"Ölü kod adayları çakışan satır aralıkları üretti: {path}")
                seen_defs.add(marker)
                replacements.append((start, end, ""))
                removed_symbols.append(str(candidate.name))
            content = self._apply(lines, replacements)
            removed_imports: tuple[str, ...] = ()
            removed_duplicate_imports = 0
            if include_imports:
                import_result = self._analyze_imports_on_content(path, content)
                content = import_result.content
                removed_imports = import_result.removed_bindings
                removed_duplicate_imports = import_result.removed_duplicates
            if content != source:
                self._parse(content, path)
                results.append(UnusedCodeCleanupResult(path, content, tuple(dict.fromkeys(removed_symbols)), removed_imports, removed_duplicate_imports))
        return tuple(results)

    def prepare(self, paths: Iterable[str | Path] | None = None, *, include_imports: bool = True, limit: int = 200) -> RefactoringPlan:
        results = self.analyze(paths, include_imports=include_imports, limit=limit)
        if not results:
            raise WorkspaceError("Güvenle temizlenebilecek kullanılmayan kod bulunamadı.")
        files: list[dict[str, str]] = []
        for result in results:
            details: list[str] = []
            if result.removed_symbols:
                details.append("semboller: " + ", ".join(result.removed_symbols))
            if result.removed_imports:
                details.append("importlar: " + ", ".join(result.removed_imports))
            if result.removed_duplicate_imports:
                details.append(f"yinelenen import: {result.removed_duplicate_imports}")
            files.append({"path": result.path, "content": result.content, "reason": "Kullanılmayan kod temizliği; " + "; ".join(details)})
        raw = json.dumps({"summary": f"{len(files)} dosyada kullanılmayan kod temizliği hazırlandı", "files": files}, ensure_ascii=False, sort_keys=True)
        return self._coordinator.prepare(raw, kind=RefactoringKind.REMOVE_UNUSED_CODE)

    def _analyze_imports_on_content(self, path: str, content: str):
        return self._imports.analyze_content(path, content)

    @staticmethod
    def _parse(source: str, path: str) -> ast.Module:
        try:
            return ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise WorkspaceError(f"Geçersiz Python dosyası: {path}:{exc.lineno}: {exc.msg}") from exc

    @staticmethod
    def _matching_definition(tree: ast.Module, candidate: DeadCodeCandidate) -> ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None:
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            expected_kind = "class" if isinstance(node, ast.ClassDef) else "function"
            if node.name == candidate.name and int(node.lineno) == int(candidate.line) and expected_kind == candidate.kind:
                return node
        return None

    @staticmethod
    def _decorated_start(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
        rows = [int(node.lineno)]
        rows.extend(int(item.lineno) for item in node.decorator_list)
        return min(rows)

    @staticmethod
    def _apply(lines: list[str], replacements: list[tuple[int, int, str]]) -> str:
        updated = list(lines)
        for start, end, replacement in sorted(replacements, reverse=True):
            if start < 0 or end < start or end > len(updated):
                raise WorkspaceError("Kod temizleme satır aralığı geçersiz.")
            updated[start:end] = [replacement] if replacement else []
        return "".join(updated)
