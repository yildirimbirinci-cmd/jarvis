from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.repository_cleanup_planner import (
    CleanupCandidate,
    CleanupPlan,
)


_SCHEMA_VERSION = 1
_EXECUTABLE_ACTIONS = frozenset({"delete", "archive"})


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class CleanupExecutionItem:
    path: str
    action: str
    status: str
    destination_path: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CleanupExecutionResult:
    schema_version: int
    execution_id: str
    source_plan_id: str
    status: str
    approved_count: int
    completed_count: int
    skipped_count: int
    failed_count: int
    reclaimed_bytes: int
    items: tuple[CleanupExecutionItem, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "source_plan_id": self.source_plan_id,
            "status": self.status,
            "approved_count": self.approved_count,
            "completed_count": self.completed_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "reclaimed_bytes": self.reclaimed_bytes,
            "items": [item.to_dict() for item in self.items],
            "warnings": list(self.warnings),
        }


class SafeRepositoryCleanupExecutor:
    """Execute explicitly approved cleanup candidates safely."""

    PROTECTED_ROOTS = frozenset(
        {
            ".git",
            ".github",
            ".venv",
            "core",
            "tests",
            "models",
            "tools",
            "docs",
        }
    )

    def __init__(
        self,
        project_root: str | Path,
        archive_root: str | Path,
        *,
        protected_roots: Iterable[str] = (),
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve(strict=False)
        )
        self.archive_root = (
            Path(archive_root).expanduser().resolve(strict=False)
        )

        if not self.project_root.is_dir():
            raise NotADirectoryError(self.project_root)
        if self.archive_root == self.project_root:
            raise ValueError(
                "cleanup archive root must not be the project root"
            )

        self.protected_roots = frozenset(
            self.PROTECTED_ROOTS
            | {
                str(item).strip()
                for item in protected_roots
                if str(item).strip()
            }
        )

    def _candidate_path(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError("cleanup path must be relative")
        if ".." in relative.parts:
            raise ValueError("cleanup path traversal is forbidden")

        first = relative.parts[0] if relative.parts else ""
        if first in self.protected_roots:
            raise ValueError("cleanup path is protected")

        resolved = (
            self.project_root / relative
        ).resolve(strict=False)
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError(
                "cleanup path escapes the project root"
            ) from exc

        if resolved.is_symlink():
            raise ValueError("cleanup symlinks are forbidden")

        return resolved

    @staticmethod
    def _candidate_index(
        plan: CleanupPlan,
    ) -> dict[str, CleanupCandidate]:
        index: dict[str, CleanupCandidate] = {}
        for candidate in plan.candidates:
            if candidate.path in index:
                raise ValueError(
                    "cleanup plan contains duplicate paths"
                )
            index[candidate.path] = candidate
        return index

    def _archive_destination(
        self,
        relative_path: str,
    ) -> Path:
        destination = (
            self.archive_root / Path(relative_path)
        ).resolve(strict=False)
        try:
            destination.relative_to(self.archive_root)
        except ValueError as exc:
            raise ValueError(
                "archive destination escapes archive root"
            ) from exc
        return destination

    def _execute_candidate(
        self,
        candidate: CleanupCandidate,
    ) -> tuple[CleanupExecutionItem, int]:
        if candidate.git_status == "tracked":
            raise ValueError(
                "tracked files cannot be cleaned automatically"
            )
        if candidate.recommended_action not in _EXECUTABLE_ACTIONS:
            raise ValueError(
                "candidate action is not executable"
            )
        if not candidate.requires_approval:
            raise ValueError(
                "executable cleanup candidate must require approval"
            )

        source = self._candidate_path(candidate.path)
        if not source.exists():
            return (
                CleanupExecutionItem(
                    path=candidate.path,
                    action=candidate.recommended_action,
                    status="skipped",
                    message="source path does not exist",
                ),
                0,
            )
        if not source.is_file():
            raise ValueError(
                "cleanup executor currently supports files only"
            )

        size_bytes = source.stat().st_size

        if candidate.recommended_action == "delete":
            source.unlink()
            return (
                CleanupExecutionItem(
                    path=candidate.path,
                    action="delete",
                    status="completed",
                    message="file deleted",
                ),
                size_bytes,
            )

        destination = self._archive_destination(
            candidate.path
        )
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            raise FileExistsError(destination)

        shutil.move(str(source), str(destination))
        return (
            CleanupExecutionItem(
                path=candidate.path,
                action="archive",
                status="completed",
                destination_path=str(destination),
                message="file archived",
            ),
            0,
        )

    def execute(
        self,
        plan: CleanupPlan,
        approved_paths: Iterable[str],
    ) -> CleanupExecutionResult:
        approved = tuple(
            dict.fromkeys(
                str(item).strip().replace("\\", "/")
                for item in approved_paths
                if str(item).strip()
            )
        )
        index = self._candidate_index(plan)

        unknown = [
            path
            for path in approved
            if path not in index
        ]
        if unknown:
            raise ValueError(
                "approved path is not present in cleanup plan"
            )

        items: list[CleanupExecutionItem] = []
        warnings: list[str] = []
        reclaimed_bytes = 0

        for path in approved:
            candidate = index[path]
            try:
                item, reclaimed = self._execute_candidate(
                    candidate
                )
            except Exception as exc:
                items.append(
                    CleanupExecutionItem(
                        path=path,
                        action=candidate.recommended_action,
                        status="failed",
                        message=str(exc),
                    )
                )
                warnings.append(
                    f"cleanup failed: {path}: {exc}"
                )
                continue

            items.append(item)
            reclaimed_bytes += reclaimed

        completed_count = sum(
            item.status == "completed"
            for item in items
        )
        skipped_count = sum(
            item.status == "skipped"
            for item in items
        )
        failed_count = sum(
            item.status == "failed"
            for item in items
        )
        status = (
            "failed"
            if failed_count and not completed_count
            else "partial"
            if failed_count
            else "completed"
        )

        identity = {
            "schema_version": _SCHEMA_VERSION,
            "source_plan_id": plan.plan_id,
            "approved_paths": list(approved),
            "items": [item.to_dict() for item in items],
        }
        execution_id = (
            "rce1-"
            + _sha256_text(
                _canonical_json(identity)
            )[:20]
        )

        return CleanupExecutionResult(
            schema_version=_SCHEMA_VERSION,
            execution_id=execution_id,
            source_plan_id=plan.plan_id,
            status=status,
            approved_count=len(approved),
            completed_count=completed_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            reclaimed_bytes=reclaimed_bytes,
            items=tuple(items),
            warnings=tuple(warnings),
        )

    def write_result(
        self,
        result: CleanupExecutionResult,
        output_path: str | Path,
    ) -> None:
        output = (
            Path(output_path).expanduser().resolve(strict=False)
        )
        if output.suffix.casefold() != ".json":
            raise ValueError(
                "cleanup execution output must be JSON"
            )
        _atomic_write_json(output, result.to_dict())
