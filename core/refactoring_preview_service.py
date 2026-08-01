"""Immutable, file-level previews for pending refactoring plans."""
from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass

from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator, RefactoringPlan
from artmach_assistant.core.workspace import WorkspaceError

_MAX_PREVIEW_FILES = 5000

def _safe_text(value: object, default: str = "") -> str:
    try:
        return str(value if value is not None else default)
    except Exception:
        return default


@dataclass(frozen=True, slots=True)
class FilePreview:
    path: str
    reason: str
    diff: str
    added_lines: int
    removed_lines: int
    existed: bool
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class RefactoringPreview:
    plan_id: str
    preview_token: str
    files: tuple[FilePreview, ...]
    added_lines: int
    removed_lines: int


class RefactoringPreviewService:
    """Builds a preview tied to the exact pending proposal snapshot."""

    def __init__(self, coordinator: RefactoringCoordinator) -> None:
        self._coordinator = coordinator

    def build(self, plan_id: str | None = None, *, max_diff_lines: int = 2000) -> RefactoringPreview:
        if isinstance(max_diff_lines, bool) or not isinstance(max_diff_lines, int) or max_diff_lines < 20:
            raise WorkspaceError("Diff satır sınırı en az 20 olmalıdır.")
        plan = self._require_plan(plan_id)
        if len(plan.proposal.files) > _MAX_PREVIEW_FILES:
            raise WorkspaceError("Refactoring önizlemesi çok fazla dosya içeriyor.")
        previews: list[FilePreview] = []
        total_added = total_removed = 0
        for change in sorted(plan.proposal.files, key=lambda item: item.path):
            lines = list(difflib.unified_diff(
                change.old_content.splitlines(keepends=True),
                change.new_content.splitlines(keepends=True),
                fromfile=f"a/{change.path}",
                tofile=f"b/{change.path}",
            ))
            added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
            removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
            truncated = len(lines) > max_diff_lines
            shown = lines[:max_diff_lines]
            if truncated:
                shown.append(f"\n... diff kısaltıldı ({len(lines) - max_diff_lines} satır gizlendi) ...\n")
            previews.append(FilePreview(
                path=_safe_text(change.path),
                reason=_safe_text(change.reason),
                diff="".join(shown),
                added_lines=added,
                removed_lines=removed,
                existed=change.existed,
                truncated=truncated,
            ))
            total_added += added
            total_removed += removed
        return RefactoringPreview(
            plan_id=plan.plan_id,
            preview_token=self._token(plan),
            files=tuple(previews),
            added_lines=total_added,
            removed_lines=total_removed,
        )

    def validate(self, preview: RefactoringPreview) -> None:
        plan = self._require_plan(preview.plan_id)
        if preview.preview_token != self._token(plan):
            raise WorkspaceError("Refactoring taslağı önizlemeden sonra değişti; yeni önizleme oluştur.")
        editor = self._coordinator._editor
        if editor.pending is not plan.proposal:
            raise WorkspaceError("Önizleme bekleyen düzenleme taslağıyla eşleşmiyor.")
        stale: list[str] = []
        for change in plan.proposal.files:
            target = editor.workspace.safe_path(change.path)
            if target.exists() != change.existed:
                stale.append(change.path)
            elif target.exists() and editor.workspace.read_text(change.path, max_chars=2_000_001) != change.old_content:
                stale.append(change.path)
        if stale:
            raise WorkspaceError("Önizlemeden sonra değişen dosyalar var: " + ", ".join(stale))

    def _require_plan(self, plan_id: str | None) -> RefactoringPlan:
        plan = self._coordinator.pending
        if plan is None:
            raise WorkspaceError("Önizlenecek bekleyen refactoring işlemi yok.")
        requested = _safe_text(plan_id).strip()
        if requested and requested != plan.plan_id:
            raise WorkspaceError("Refactoring işlem kimliği bekleyen taslakla eşleşmiyor.")
        return plan

    @staticmethod
    def _token(plan: RefactoringPlan) -> str:
        digest = hashlib.sha256()
        digest.update(plan.plan_id.encode("utf-8"))
        for change in sorted(plan.proposal.files, key=lambda item: item.path):
            for value in (change.path, change.reason, change.old_content, change.new_content, str(change.existed)):
                digest.update(_safe_text(value).encode("utf-8"))
                digest.update(b"\0")
        return digest.hexdigest()
