"""Workspace-wide refactoring orchestration over bounded, approved batches."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

MAX_PATTERNS = 32
MAX_PATTERN_LENGTH = 500
MAX_OPERATION_LENGTH = 500

from artmach_assistant.core.multi_file_refactoring import RefactoringPatch

try:
    from artmach_assistant.core.workspace import WorkspaceError
except ModuleNotFoundError:  # Lightweight/isolated test environments.
    class WorkspaceError(RuntimeError):
        pass


class MultiFileRefactoringProtocol(Protocol):
    def prepare(
        self,
        patches: Iterable[RefactoringPatch],
        *,
        summary: str = "Çok dosyalı refactoring",
    ) -> object: ...


class RefactoringBuilderProtocol(Protocol):
    def build(self, path: str) -> object | None: ...


@dataclass(frozen=True, slots=True)
class WorkspaceRefactoringBatch:
    batch_number: int
    paths: tuple[str, ...]
    plan: object


@dataclass(frozen=True, slots=True)
class WorkspaceRefactoringPlan:
    operation: str
    matched_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...]
    batches: tuple[WorkspaceRefactoringBatch, ...]

    @property
    def changed_file_count(self) -> int:
        return len(self.matched_paths) - len(self.skipped_paths)


class WorkspaceWideRefactoring:
    """Scan a workspace and prepare safe multi-file plans in bounded batches."""

    EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", ".artmach_assistant"}

    def __init__(
        self,
        multi_file: MultiFileRefactoringProtocol,
        *,
        max_files_per_batch: int = 8,
        max_workspace_files: int = 5000,
    ) -> None:
        if isinstance(max_files_per_batch, bool) or max_files_per_batch < 1 or max_files_per_batch > 8:
            raise ValueError("Batch başına dosya sayısı 1 ile 8 arasında olmalı.")
        self._multi_file = multi_file
        self._max_files_per_batch = max_files_per_batch
        if isinstance(max_workspace_files, bool):
            raise ValueError("Workspace dosya sınırı tam sayı olmalı.")
        self._max_workspace_files = max(1, min(int(max_workspace_files), 100_000))

    def discover(self, root: str | Path, patterns: Iterable[str] = ("*.py",)) -> tuple[str, ...]:
        project_root = Path(root).expanduser().resolve()
        if not project_root.is_dir():
            raise WorkspaceError("Workspace refactoring için geçerli proje kökü gerekli.")
        found: set[str] = set()
        clean_patterns = self._patterns(patterns)
        for clean in clean_patterns:
            if not clean:
                continue
            for path in project_root.rglob(clean):
                if any(part in self.EXCLUDED_PARTS for part in path.relative_to(project_root).parts):
                    continue
                if path.is_symlink():
                    continue
                if path.is_file():
                    found.add(path.relative_to(project_root).as_posix())
                    if len(found) > self._max_workspace_files:
                        raise WorkspaceError("Workspace dosya sayısı güvenlik sınırını aşıyor.")
        return tuple(sorted(found, key=str.casefold))

    def prepare(
        self,
        root: str | Path,
        builder: RefactoringBuilderProtocol,
        *,
        operation: str,
        patterns: Iterable[str] = ("*.py",),
    ) -> WorkspaceRefactoringPlan:
        normalized_operation = self._safe_text(operation or "workspace_refactoring", MAX_OPERATION_LENGTH).strip() or "workspace_refactoring"
        paths = self.discover(root, patterns)
        proposals: list[tuple[str, object]] = []
        skipped: list[str] = []
        for path in paths:
            try:
                proposal = builder.build(path)
            except Exception:
                skipped.append(path)
                continue
            if proposal is None:
                skipped.append(path)
            else:
                proposals.append((path, proposal))

        batches: list[WorkspaceRefactoringBatch] = []
        for index in range(0, len(proposals), self._max_files_per_batch):
            chunk = proposals[index:index + self._max_files_per_batch]
            plan = self._multi_file.prepare(
                [
                    RefactoringPatch(normalized_operation, proposal)
                    for _, proposal in chunk
                ],
                summary=f"Workspace refactoring: {normalized_operation}",
            )
            batches.append(WorkspaceRefactoringBatch(
                batch_number=len(batches) + 1,
                paths=tuple(path for path, _ in chunk),
                plan=plan,
            ))
        return WorkspaceRefactoringPlan(
            operation=normalized_operation,
            matched_paths=paths,
            skipped_paths=tuple(skipped),
            batches=tuple(batches),
        )


    @staticmethod
    def _safe_text(value: object, limit: int) -> str:
        try:
            text = str(value or "")
        except Exception:
            text = ""
        return text.replace("\x00", "")[:limit]

    @classmethod
    def _patterns(cls, patterns: Iterable[str]) -> tuple[str, ...]:
        try:
            iterator = iter(patterns)
        except Exception as exc:
            raise WorkspaceError("Workspace pattern listesi okunamadı.") from exc
        result: list[str] = []
        for _ in range(MAX_PATTERNS + 1):
            try:
                value = next(iterator)
            except StopIteration:
                break
            except Exception as exc:
                raise WorkspaceError("Workspace pattern listesi okunurken hata oluştu.") from exc
            clean = cls._safe_text(value, MAX_PATTERN_LENGTH).strip()
            if not clean:
                continue
            if clean.startswith(("/", "\\")) or ".." in Path(clean).parts:
                raise WorkspaceError("Workspace arama patterni proje dışına çıkamaz.")
            result.append(clean)
        if len(result) > MAX_PATTERNS:
            raise WorkspaceError("Workspace pattern sayısı güvenlik sınırını aşıyor.")
        return tuple(dict.fromkeys(result))
