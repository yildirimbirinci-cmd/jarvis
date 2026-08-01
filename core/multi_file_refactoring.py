"""Compose several safe refactoring proposals into one approval transaction."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

MAX_PATCHES = 16
MAX_FILES = 8
MAX_TEXT = 2_000_000

from artmach_assistant.core.edit_manager import EditProposal, ProposedFileChange
from artmach_assistant.core.refactoring_coordinator import (
    RefactoringCoordinator,
    RefactoringKind,
    RefactoringPlan,
)
from artmach_assistant.core.workspace import WorkspaceError


@dataclass(frozen=True, slots=True)
class RefactoringPatch:
    operation: str
    proposal: EditProposal


class MultiFileRefactoring:
    """Merge independent proposals without writing files before approval."""

    _ORDER = {
        "rename_symbol": 10,
        "move_class": 20,
        "move_function": 20,
        "extract_method": 30,
        "inline_method": 40,
        "optimize_imports": 50,
        "remove_unused_code": 60,
    }

    def __init__(self, coordinator: RefactoringCoordinator) -> None:
        self._coordinator = coordinator

    def prepare(
        self,
        patches: Iterable[RefactoringPatch],
        *,
        summary: str = "Çok dosyalı refactoring",
    ) -> RefactoringPlan:
        rows = self._bounded_patches(patches)
        if not rows:
            raise WorkspaceError("Birleştirilecek refactoring işlemi yok.")
        if len(rows) > MAX_PATCHES:
            raise WorkspaceError("Tek planda en fazla 16 refactoring işlemi birleştirilebilir.")

        rows.sort(key=lambda item: (self._ORDER.get(self._safe_text(getattr(item, "operation", ""), 100), 999), self._safe_text(getattr(item, "operation", ""), 100)))
        merged: dict[str, ProposedFileChange] = {}
        for patch in rows:
            proposal = getattr(patch, "proposal", None)
            files = getattr(proposal, "files", None)
            if files is None:
                raise WorkspaceError("Refactoring patch geçerli bir proposal içermiyor.")
            for change in self._bounded_changes(files):
                path = self._safe_text(getattr(change, "path", ""), 20_000).strip().replace("\\", "/")
                if not path or "\x00" in path:
                    raise WorkspaceError("Refactoring değişikliği geçerli bir dosya yolu içermiyor.")
                old_content = getattr(change, "old_content", None)
                new_content = getattr(change, "new_content", None)
                if not isinstance(old_content, str) or not isinstance(new_content, str):
                    raise WorkspaceError(f"Refactoring içeriği metin olmalı: {path}")
                if len(old_content) > MAX_TEXT or len(new_content) > MAX_TEXT:
                    raise WorkspaceError(f"Refactoring içeriği güvenlik sınırını aşıyor: {path}")
                existing = merged.get(path)
                if existing is None:
                    merged[path] = change
                    continue
                if existing.old_content != old_content:
                    raise WorkspaceError(
                        f"Çakışan başlangıç içeriği nedeniyle işlemler birleştirilemedi: {change.path}"
                    )
                if existing.new_content != new_content:
                    raise WorkspaceError(
                        f"Aynı dosya için çakışan refactoring çıktıları var: {change.path}"
                    )

        if len(merged) > MAX_FILES:
            raise WorkspaceError("Birleşik refactoring en fazla 8 dosya değiştirebilir.")

        payload = {
            "summary": self._safe_text(summary or "Çok dosyalı refactoring", 20_000),
            "files": [
                {
                    "path": change.path,
                    "reason": change.reason,
                    "content": change.new_content,
                }
                for change in sorted(merged.values(), key=lambda item: item.path)
            ],
        }
        return self._coordinator.prepare(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            kind=RefactoringKind.MULTI_FILE,
        )


    @staticmethod
    def _safe_text(value: object, limit: int) -> str:
        try:
            text = str(value or "")
        except Exception:
            text = ""
        return text.replace("\x00", "")[:limit]

    @staticmethod
    def _bounded_patches(patches: Iterable[RefactoringPatch]) -> list[RefactoringPatch]:
        try:
            iterator = iter(patches)
        except Exception as exc:
            raise WorkspaceError("Refactoring patch listesi okunamadı.") from exc
        rows: list[RefactoringPatch] = []
        for _ in range(MAX_PATCHES + 1):
            try:
                rows.append(next(iterator))
            except StopIteration:
                return rows
            except Exception as exc:
                raise WorkspaceError("Refactoring patch listesi okunurken hata oluştu.") from exc
        return rows

    @staticmethod
    def _bounded_changes(changes: object):
        try:
            iterator = iter(changes)
        except Exception as exc:
            raise WorkspaceError("Proposal dosya listesi okunamadı.") from exc
        for _ in range(MAX_FILES + 1):
            try:
                yield next(iterator)
            except StopIteration:
                return
            except Exception as exc:
                raise WorkspaceError("Proposal dosya listesi okunurken hata oluştu.") from exc
