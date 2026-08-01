"""Final integration facade for the SAE 8.4 refactoring pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Mapping

_MAX_MESSAGE_CHARS = 8192

def _safe_text(value: object, default: str = "") -> str:
    try:
        text = str(value if value is not None else default)
    except Exception:
        text = default
    return text[:_MAX_MESSAGE_CHARS]

from artmach_assistant.core.background_refactoring_queue import (
    BackgroundRefactoringQueue,
    RefactoringJob,
    RefactoringJobResult,
    RefactoringPriority,
)
from artmach_assistant.core.refactoring_coordinator import (
    RefactoringCoordinator,
    RefactoringKind,
    RefactoringPlan,
)
from artmach_assistant.core.refactoring_preview_service import (
    RefactoringPreview,
    RefactoringPreviewService,
)
from artmach_assistant.core.refactoring_transaction_history import (
    RefactoringTransactionHistory,
)
from artmach_assistant.core.regression_safety_check import (
    RegressionReport,
    RegressionSafetyCheck,
)
from artmach_assistant.core.workspace import WorkspaceError


@dataclass(frozen=True, slots=True)
class RefactoringExecutionResult:
    plan_id: str
    apply_message: str
    regression: RegressionReport
    rolled_back: bool = False


class AIRefactoringEngine:
    """One safe entry point for prepare, preview, apply, rollback and queueing.

    The engine never auto-applies background work. Every change must first be
    represented by a pending coordinator plan and validated against a preview.
    """

    def __init__(
        self,
        coordinator: RefactoringCoordinator,
        *,
        preview_service: RefactoringPreviewService | None = None,
        regression_check: RegressionSafetyCheck | None = None,
        history: RefactoringTransactionHistory | None = None,
        queue: BackgroundRefactoringQueue | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._preview = preview_service or RefactoringPreviewService(coordinator)
        self._regression = regression_check or RegressionSafetyCheck()
        workspace = coordinator._editor.workspace
        self._history = history or RefactoringTransactionHistory(workspace)
        self._queue = queue or BackgroundRefactoringQueue()

    @property
    def pending(self) -> RefactoringPlan | None:
        return self._coordinator.pending

    def prepare(
        self,
        raw_response: str,
        *,
        kind: RefactoringKind | str,
        symbol: str = "",
        new_name: str = "",
        rename_safety: object | None = None,
    ) -> RefactoringPlan:
        return self._coordinator.prepare(
            raw_response,
            kind=kind,
            symbol=symbol,
            new_name=new_name,
            rename_safety=rename_safety,
        )

    def preview(self, plan_id: str | None = None, *, max_diff_lines: int = 2000) -> RefactoringPreview:
        return self._preview.build(plan_id, max_diff_lines=max_diff_lines)

    def apply(
        self,
        preview: RefactoringPreview,
        *,
        expected_symbols: Mapping[str, tuple[str, ...] | list[str]] | None = None,
        check_imports: bool = False,
    ) -> RefactoringExecutionResult:
        """Apply the exact previewed plan and rollback on regression errors."""
        self._preview.validate(preview)
        plan = self._coordinator.pending
        if plan is None or plan.plan_id != preview.plan_id:
            raise WorkspaceError("Önizleme ile bekleyen refactoring planı eşleşmiyor.")
        if check_imports and getattr(self._regression, "_import_resolver", None) is None:
            raise WorkspaceError("Import regresyon kontrolü için import resolver yapılandırılmamış.")

        changed_paths = plan.changed_paths
        root = Path(self._coordinator._editor.workspace.require_root())
        apply_message = self._coordinator.apply(plan.plan_id)
        try:
            report = self._regression.check(
                root,
                changed_paths,
                expected_symbols=dict(expected_symbols or {}),
            )
        except Exception as exc:
            self._rollback_after_failure(
                "Refactoring uygulandı ancak regresyon kontrolü tamamlanamadı", exc
            )
            raise AssertionError("unreachable")

        if report.rollback_required:
            self._rollback_after_failure(
                "Refactoring regresyon kontrolünden geçemedi", None
            )
            return RefactoringExecutionResult(
                plan_id=plan.plan_id,
                apply_message=apply_message,
                regression=report,
                rolled_back=True,
            )
        return RefactoringExecutionResult(plan.plan_id, apply_message, report, False)

    def _rollback_after_failure(self, context: str, cause: Exception | None) -> None:
        try:
            self._history.undo()
        except Exception as rollback_exc:
            message = f"{context} ve otomatik rollback başarısız oldu: {_safe_text(rollback_exc, 'bilinmeyen hata')}"
            if cause is not None:
                message += f" (asıl hata: {_safe_text(cause, 'bilinmeyen hata')})"
            raise WorkspaceError(message) from rollback_exc
        if cause is not None:
            raise WorkspaceError(f"{context}; değişiklikler geri alındı: {_safe_text(cause, 'bilinmeyen hata')}") from cause

    def reject(self, plan_id: str | None = None) -> str:
        return self._coordinator.reject(plan_id)

    def undo(self) -> str:
        return self._history.undo()

    def redo(self) -> str:
        return self._history.redo()

    def submit_background(
        self,
        key: str,
        callback,
        *,
        priority: RefactoringPriority = RefactoringPriority.NORMAL,
    ) -> RefactoringJob | None:
        """Queue analysis/planning only; reject callbacks returning applied results."""
        def guarded(cancel_event: Event) -> RefactoringJobResult:
            value = callback(cancel_event)
            if isinstance(value, RefactoringExecutionResult):
                raise WorkspaceError("Arka plan işi refactoring değişikliğini otomatik uygulayamaz.")
            if isinstance(value, RefactoringJobResult):
                return value
            return RefactoringJobResult(_safe_text(value, "Refactoring taslağı hazır."), value)

        return self._queue.submit(key, guarded, priority=priority)
