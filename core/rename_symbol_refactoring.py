"""Index-guided, user-approved Rename Symbol refactoring.

Only the exact definition/reference locations returned by the existing rename
safety analysis are edited.  Files are never written before coordinator
approval.
"""
from __future__ import annotations

import json
import keyword
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MAX_SYMBOL_TEXT = 20_000
MAX_LOCATIONS = 50_000

from artmach_assistant.core.refactoring_coordinator import (
    RefactoringCoordinator,
    RefactoringKind,
    RefactoringPlan,
)
from artmach_assistant.core.workspace import WorkspaceError


@dataclass(frozen=True, slots=True)
class RenameSymbolRequest:
    old_name: str
    new_name: str


class RenameSymbolRefactoring:
    def __init__(self, coordinator: RefactoringCoordinator, safety_analyzer: object) -> None:
        self._coordinator = coordinator
        self._safety_analyzer = safety_analyzer

    def prepare(self, request: RenameSymbolRequest) -> RefactoringPlan:
        old_name = self._safe_text(getattr(request, "old_name", "")).strip()
        new_name = self._safe_text(getattr(request, "new_name", "")).strip()
        if not old_name:
            raise WorkspaceError("Yeniden adlandırılacak sembol zorunludur.")
        if not new_name.isidentifier() or keyword.iskeyword(new_name):
            raise WorkspaceError("Yeni sembol adı geçerli bir Python tanımlayıcısı olmalı.")

        try:
            safety = self._safety_analyzer.analyze(old_name, new_name)
        except Exception as exc:
            raise WorkspaceError(f"Rename güvenlik analizi başarısız: {self._safe_text(exc, 500)}") from exc
        if not bool(getattr(safety, "safe", False)):
            raise WorkspaceError("Rename güvenlik analizi işlemi engelledi.")

        old_token = old_name.rsplit(".", 1)[-1]
        locations = self._locations(safety)
        if not locations:
            raise WorkspaceError("Yeniden adlandırılacak tanım veya referans bulunamadı.")

        grouped: dict[str, list[tuple[int, int]]] = {}
        for path, line, column in locations:
            grouped.setdefault(path, []).append((line, column))
        if len(grouped) > 8:
            raise WorkspaceError("Rename işlemi tek transaction sınırı olan 8 dosyayı aşıyor.")

        workspace = self._coordinator._editor.workspace
        files: list[dict[str, str]] = []
        for path in sorted(grouped, key=str.casefold):
            relative = self._relative_path(workspace.require_root(), path)
            source = workspace.read_text(relative, max_chars=2_000_001)
            updated = self._replace_locations(source, grouped[path], old_token, new_name, relative)
            if updated != source:
                files.append({
                    "path": relative,
                    "content": updated,
                    "reason": f"{old_name} sembolünün indekslenmiş tanım ve referanslarını {new_name} olarak değiştir.",
                })
        if not files:
            raise WorkspaceError("Rename işlemi gerçek bir dosya değişikliği üretmedi.")

        raw = json.dumps({
            "summary": f"{old_name} sembolü {new_name} olarak yeniden adlandırıldı",
            "files": files,
        }, ensure_ascii=False)
        return self._coordinator.prepare(
            raw,
            kind=RefactoringKind.RENAME_SYMBOL,
            symbol=old_name,
            new_name=new_name,
            rename_safety=safety,
        )

    @staticmethod
    def _locations(safety: object) -> tuple[tuple[str, int, int], ...]:
        unique: set[tuple[str, int, int]] = set()
        sources: list[object] = []
        target = getattr(safety, "target", None)
        sources.extend((getattr(target, "definitions", ()), getattr(target, "references", ())))
        impact = getattr(safety, "impact", None)
        for item in RenameSymbolRefactoring._bounded(getattr(impact, "files", ())):
            sources.extend((getattr(item, "definitions", ()), getattr(item, "references", ())))
        for source in sources:
            for item in RenameSymbolRefactoring._bounded(source):
                path = RenameSymbolRefactoring._safe_text(getattr(item, "path", "")).strip()
                try:
                    line = int(getattr(item, "line", 0) or 0)
                    column = int(getattr(item, "column", 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    continue
                if path and line > 0 and column >= 0:
                    unique.add((path, line, column))
                    if len(unique) > MAX_LOCATIONS:
                        raise WorkspaceError("Rename konum sayısı güvenlik sınırını aşıyor.")
        return tuple(sorted(unique, key=lambda row: (row[0].casefold(), row[1], row[2])))

    @staticmethod
    def _relative_path(root: Path, value: str) -> str:
        root = Path(root).resolve(strict=False)
        path = Path(value).expanduser()
        absolute = path.resolve(strict=False) if path.is_absolute() else (root / path).resolve(strict=False)
        try:
            return absolute.relative_to(root).as_posix()
        except ValueError as exc:
            raise WorkspaceError(f"İndeks kaydı proje dışında: {value}") from exc

    @staticmethod
    def _replace_locations(
        source: str,
        locations: Iterable[tuple[int, int]],
        old_token: str,
        new_name: str,
        path: str,
    ) -> str:
        lines = source.splitlines(keepends=True)
        offsets: list[int] = []
        total = 0
        for line in lines:
            offsets.append(total)
            total += len(line)
        edits: list[tuple[int, int]] = []
        for line, column in locations:
            if line > len(lines):
                raise WorkspaceError(f"Eski indeks konumu dosya dışında: {path}:{line}")
            start = offsets[line - 1] + column
            end = start + len(old_token)
            if source[start:end] != old_token:
                raise WorkspaceError(
                    f"Dosya indekslendikten sonra değişmiş; rename uygulanmadı: {path}:{line}:{column}"
                )
            before = source[start - 1] if start else ""
            after = source[end] if end < len(source) else ""
            if (before and (before.isalnum() or before == "_")) or (after and (after.isalnum() or after == "_")):
                raise WorkspaceError(f"İndeks konumu tam sembol sınırında değil: {path}:{line}:{column}")
            edits.append((start, end))
        result = source
        for start, end in sorted(set(edits), reverse=True):
            result = result[:start] + new_name + result[end:]
        if path.endswith(".py"):
            try:
                compile(result, path, "exec")
            except SyntaxError as exc:
                raise WorkspaceError(f"Rename sonucu geçersiz Python üretti: {exc}") from exc
        return result


    @staticmethod
    def _safe_text(value: object, limit: int = MAX_SYMBOL_TEXT) -> str:
        try:
            text = str(value or "")
        except Exception:
            text = ""
        return text.replace("\x00", "")[:limit]

    @staticmethod
    def _bounded(items: object, limit: int = MAX_LOCATIONS):
        try:
            iterator = iter(items or ())
        except Exception:
            return
        for _ in range(limit):
            try:
                yield next(iterator)
            except StopIteration:
                return
            except Exception:
                return
