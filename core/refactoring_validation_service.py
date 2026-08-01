"""Final validation gate for pending refactoring transactions.

This service validates the exact proposal snapshot immediately before apply.
It complements the early PatchValidator check by detecting stale workspace
files, mutated proposals, duplicate paths and unsupported content at the last
safe point before any project file is replaced.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.patch_validator import PatchValidator
from artmach_assistant.core.path_normalizer import path_key
from artmach_assistant.core.workspace import WorkspaceError

_MAX_MESSAGE_CHARS = 8192

def _safe_text(value: object, default: str = "") -> str:
    try:
        return str(value if value is not None else default)[:_MAX_MESSAGE_CHARS]
    except Exception:
        return default


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class RefactoringValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str
    path: str = ""
    line: int | None = None


@dataclass(frozen=True, slots=True)
class RefactoringValidationReport:
    plan_id: str
    validation_token: str
    issues: tuple[RefactoringValidationIssue, ...]
    checked_files: tuple[str, ...]

    @property
    def errors(self) -> tuple[RefactoringValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[RefactoringValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is ValidationSeverity.WARNING)

    @property
    def is_valid(self) -> bool:
        return not self.errors


class RefactoringValidationService:
    """Validate a pending plan without writing to the workspace."""

    def __init__(self, patch_validator: PatchValidator | None = None) -> None:
        self._patch_validator = patch_validator or PatchValidator()

    def validate(self, editor: object, plan: object) -> RefactoringValidationReport:
        proposal = getattr(plan, "proposal", None)
        plan_id = _safe_text(getattr(plan, "plan_id", "")).strip()
        issues: list[RefactoringValidationIssue] = []

        if proposal is None or not plan_id:
            issues.append(self._error("invalid_plan", "Refactoring planı eksik veya geçersiz."))
            return RefactoringValidationReport(plan_id, "", tuple(issues), ())

        if getattr(editor, "pending", None) is not proposal:
            issues.append(self._error(
                "pending_mismatch",
                "Refactoring planı bekleyen düzenleme taslağıyla eşleşmiyor.",
            ))

        files = tuple(getattr(proposal, "files", ()) or ())
        if not files:
            issues.append(self._error("empty_proposal", "Refactoring taslağı dosya değişikliği içermiyor."))
            return RefactoringValidationReport(plan_id, self.token(plan), tuple(issues), ())

        workspace = getattr(editor, "workspace", None)
        if workspace is None:
            issues.append(self._error("missing_workspace", "Düzenleme yöneticisinin çalışma alanı yok."))
            return RefactoringValidationReport(plan_id, self.token(plan), tuple(issues), ())

        root = workspace.require_root()
        seen: set[str] = set()
        checked: list[str] = []
        for change in files:
            path = _safe_text(getattr(change, "path", "")).replace("\\", "/").strip()
            if not path:
                issues.append(self._error("empty_path", "Dosya yolu boş olamaz."))
                continue
            try:
                target = workspace.safe_path(path)
            except Exception as exc:
                issues.append(self._error("unsafe_path", f"Dosya yolu güvenli değil: {exc}", path))
                continue

            key = path_key(target)
            if key in seen:
                issues.append(self._error("duplicate_path", "Aynı dosya taslakta birden fazla kez bulunuyor.", path))
                continue
            seen.add(key)
            checked.append(path)

            old_content = getattr(change, "old_content", None)
            new_content = getattr(change, "new_content", None)
            existed = bool(getattr(change, "existed", False))
            if not isinstance(old_content, str) or not isinstance(new_content, str):
                issues.append(self._error("invalid_content", "Eski ve yeni içerik metin olmalıdır.", path))
                continue
            if old_content == new_content:
                issues.append(self._error("no_change", "Dosya değişikliği gerçek bir içerik farkı taşımıyor.", path))

            exists_now = target.exists()
            if exists_now != existed:
                issues.append(self._error(
                    "stale_file_state",
                    "Dosyanın varlık durumu taslak hazırlandıktan sonra değişti.",
                    path,
                ))
            elif exists_now:
                try:
                    current = workspace.read_text(path, max_chars=2_000_001)
                except Exception as exc:
                    issues.append(self._error("read_failed", f"Dosya yeniden okunamadı: {exc}", path))
                else:
                    if current != old_content:
                        issues.append(self._error(
                            "stale_content",
                            "Dosya içeriği taslak hazırlandıktan sonra değişti.",
                            path,
                        ))

        try:
            patch_result = self._patch_validator.validate(Path(root), files)
        except Exception as exc:
            issues.append(self._error("patch_validator_failed", "Patch doğrulayıcı çalıştırılamadı: " + _safe_text(exc, "bilinmeyen hata")))
            patch_result = None
        for issue in tuple(getattr(patch_result, "issues", ()) or ()) if patch_result is not None else ():
            issues.append(RefactoringValidationIssue(
                severity=ValidationSeverity.ERROR,
                code=_safe_text(getattr(issue, "code", "patch_validation"), "patch_validation"),
                message=_safe_text(getattr(issue, "message", "Patch doğrulaması başarısız."), "Patch doğrulaması başarısız."),
                path=_safe_text(getattr(issue, "path", "")),
                line=getattr(issue, "line", None),
            ))

        warnings = tuple(text for row in tuple(getattr(plan, "warnings", ()) or ()) if (text := _safe_text(row).strip()))
        for warning in dict.fromkeys(warnings):
            issues.append(RefactoringValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="plan_warning",
                message=warning,
            ))

        return RefactoringValidationReport(
            plan_id=plan_id,
            validation_token=self.token(plan),
            issues=tuple(issues),
            checked_files=tuple(sorted(checked)),
        )

    def validate_or_raise(self, editor: object, plan: object) -> RefactoringValidationReport:
        report = self.validate(editor, plan)
        if report.is_valid:
            return report
        rows: list[str] = []
        for issue in report.errors[:20]:
            location = issue.path
            if issue.line is not None and issue.line > 0:
                location = f"{location}:{issue.line}" if location else f"satır {issue.line}"
            prefix = f"{location}: " if location else ""
            rows.append(f"- {prefix}{issue.message} [{issue.code}]")
        if len(report.errors) > 20:
            rows.append(f"- … {len(report.errors) - 20} ek doğrulama hatası")
        raise WorkspaceError("Refactoring son doğrulamadan geçemedi:\n" + "\n".join(rows))

    @staticmethod
    def token(plan: object) -> str:
        digest = hashlib.sha256()
        digest.update(_safe_text(getattr(plan, "plan_id", "")).encode("utf-8"))
        proposal = getattr(plan, "proposal", None)
        for change in sorted(tuple(getattr(proposal, "files", ()) or ()), key=lambda item: _safe_text(getattr(item, "path", ""))):
            values: Iterable[object] = (
                getattr(change, "path", ""),
                getattr(change, "reason", ""),
                getattr(change, "old_content", ""),
                getattr(change, "new_content", ""),
                getattr(change, "existed", False),
            )
            for value in values:
                digest.update(_safe_text(value).encode("utf-8"))
                digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _error(code: str, message: str, path: str = "", line: int | None = None) -> RefactoringValidationIssue:
        return RefactoringValidationIssue(ValidationSeverity.ERROR, code, message, path, line)
