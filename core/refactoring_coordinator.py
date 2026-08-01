"""Safe coordinator for staged, user-approved refactoring operations.

The coordinator owns the lifecycle around an :class:`EditProposal`: prepare,
validate, preview, approve/apply, or reject.  It deliberately delegates file
writes and checkpoint rollback to ``EditManager`` so every refactoring keeps
one consistent transaction boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable
from uuid import uuid4

from artmach_assistant.core.edit_manager import EditManager, EditProposal
from artmach_assistant.core.workspace import WorkspaceError

_MAX_TEXT_CHARS = 8192

def _safe_text(value: object, default: str = "") -> str:
    try:
        return str(value if value is not None else default)[:_MAX_TEXT_CHARS]
    except Exception:
        return default


class RefactoringKind(str, Enum):
    EXTRACT_METHOD = "extract_method"
    INLINE_METHOD = "inline_method"
    RENAME_SYMBOL = "rename_symbol"
    MOVE_CLASS = "move_class"
    MOVE_FUNCTION = "move_function"
    OPTIMIZE_IMPORTS = "optimize_imports"
    REMOVE_UNUSED_CODE = "remove_unused_code"
    MULTI_FILE = "multi_file"
    OTHER = "other"


@runtime_checkable
class PatchValidatorProtocol(Protocol):
    def validate(self, root: object, changes: object) -> object: ...


@dataclass(frozen=True, slots=True)
class RefactoringPlan:
    plan_id: str
    kind: RefactoringKind
    proposal: EditProposal
    created_at: str
    symbol: str = ""
    new_name: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(change.path for change in self.proposal.files)

    def preview(self) -> str:
        return self.proposal.diff_text()


class RefactoringCoordinator:
    """Coordinates validation and explicit approval for one pending refactor."""

    def __init__(
        self,
        editor: EditManager,
        validator: PatchValidatorProtocol | None = None,
        final_validator: object | None = None,
    ) -> None:
        self._editor = editor
        if validator is None:
            # Imported lazily so the coordinator module stays importable during
            # staged upgrades where PatchValidator has not been installed yet.
            from artmach_assistant.core.patch_validator import PatchValidator

            validator = PatchValidator()
        self._validator = validator
        if final_validator is None:
            from artmach_assistant.core.refactoring_validation_service import (
                RefactoringValidationService,
            )

            final_validator = RefactoringValidationService()
        self._final_validator = final_validator
        self._pending: RefactoringPlan | None = None

    @property
    def pending(self) -> RefactoringPlan | None:
        return self._pending

    def prepare(
        self,
        raw_response: str,
        *,
        kind: RefactoringKind | str,
        symbol: str = "",
        new_name: str = "",
        rename_safety: object | None = None,
    ) -> RefactoringPlan:
        """Create and validate a proposal without modifying workspace files."""
        if self._pending is not None or self._editor.pending is not None:
            raise WorkspaceError(
                "Önce bekleyen refactoring işlemini uygula veya reddet."
            )

        operation = self._coerce_kind(kind)
        symbol = _safe_text(symbol).strip()
        new_name = _safe_text(new_name).strip()
        warnings = self._validate_preconditions(
            operation,
            symbol=symbol,
            new_name=new_name,
            rename_safety=rename_safety,
        )

        proposal: EditProposal | None = None
        try:
            proposal = self._editor.create_proposal(raw_response)
            result = self._validator.validate(
                self._editor.workspace.require_root(), proposal.files
            )
            issues = tuple(getattr(result, "issues", ()) or ())
            if issues or not bool(getattr(result, "is_valid", not issues)):
                raise WorkspaceError(self._format_validation_issues(issues))
        except Exception:
            # create_proposal makes the proposal pending before validation.  A
            # failed validation must never leave an approvable unsafe proposal.
            self._editor.pending = None
            self._pending = None
            raise

        plan = RefactoringPlan(
            plan_id=uuid4().hex,
            kind=operation,
            proposal=proposal,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            symbol=symbol,
            new_name=new_name,
            warnings=warnings,
        )
        self._pending = plan
        return plan

    def preview(self, plan_id: str | None = None) -> str:
        plan = self._require_pending(plan_id)
        return plan.preview()

    def apply(self, plan_id: str | None = None) -> str:
        plan = self._require_pending(plan_id)
        if self._editor.pending is not plan.proposal:
            self._clear_pending()
            raise WorkspaceError(
                "Refactoring taslağı ile düzenleme taslağı eşleşmiyor; işlem iptal edildi."
            )
        validate_or_raise = getattr(self._final_validator, "validate_or_raise", None)
        if not callable(validate_or_raise):
            raise WorkspaceError("Refactoring son doğrulama servisi kullanılamıyor.")
        validate_or_raise(self._editor, plan)
        try:
            return self._editor.apply()
        finally:
            self._pending = None

    def reject(self, plan_id: str | None = None) -> str:
        self._require_pending(plan_id)
        self._pending = None
        return self._editor.reject()

    def _require_pending(self, plan_id: str | None) -> RefactoringPlan:
        plan = self._pending
        if plan is None:
            raise WorkspaceError("Bekleyen refactoring işlemi yok.")
        requested = _safe_text(plan_id).strip()
        if requested and requested != plan.plan_id:
            raise WorkspaceError("Refactoring işlem kimliği bekleyen taslakla eşleşmiyor.")
        return plan

    def _clear_pending(self) -> None:
        self._pending = None
        self._editor.pending = None

    @staticmethod
    def _coerce_kind(value: RefactoringKind | str) -> RefactoringKind:
        if isinstance(value, RefactoringKind):
            return value
        try:
            return RefactoringKind(_safe_text(value).strip())
        except ValueError as exc:
            raise WorkspaceError(f"Desteklenmeyen refactoring türü: {_safe_text(value, '<geçersiz>')}") from exc

    @staticmethod
    def _validate_preconditions(
        kind: RefactoringKind,
        *,
        symbol: str,
        new_name: str,
        rename_safety: object | None,
    ) -> tuple[str, ...]:
        if kind is not RefactoringKind.RENAME_SYMBOL:
            return ()
        if not symbol or not new_name:
            raise WorkspaceError(
                "Sembol yeniden adlandırma için mevcut ve yeni ad zorunludur."
            )
        if rename_safety is None:
            raise WorkspaceError(
                "Sembol yeniden adlandırma için güvenlik analizi zorunludur."
            )
        if not bool(getattr(rename_safety, "safe", False)):
            raise WorkspaceError("Rename güvenlik analizi işlemi engelledi.")

        warnings: list[str] = []
        for issue in tuple(getattr(rename_safety, "issues", ()) or ()):
            level = getattr(issue, "level", "")
            level_value = _safe_text(getattr(level, "value", level)).casefold()
            if level_value == "warning":
                message = _safe_text(getattr(issue, "message", "")).strip()
                if message:
                    warnings.append(message)
        return tuple(dict.fromkeys(warnings))

    @staticmethod
    def _format_validation_issues(issues: tuple[object, ...]) -> str:
        if not issues:
            return "Patch doğrulaması başarısız oldu."
        rows: list[str] = []
        for issue in issues[:20]:
            path = _safe_text(getattr(issue, "path", ""), "<bilinmeyen dosya>") or "<bilinmeyen dosya>"
            line = getattr(issue, "line", None)
            message = _safe_text(getattr(issue, "message", ""), "Doğrulama hatası") or "Doğrulama hatası"
            location = f"{path}:{line}" if isinstance(line, int) and line > 0 else path
            rows.append(f"- {location}: {message}")
        if len(issues) > 20:
            rows.append(f"- … {len(issues) - 20} ek doğrulama hatası")
        return "Refactoring patch doğrulamasından geçemedi:\n" + "\n".join(rows)
