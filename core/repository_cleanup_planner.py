from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

from artmach_assistant.core.repository_inventory import (
    RepositoryFile,
    RepositoryInventory,
)


_SCHEMA_VERSION = 1
_ALLOWED_ACTIONS = frozenset(
    {"keep", "review", "delete", "archive"}
)
_ALLOWED_RISKS = frozenset({"low", "medium", "high"})


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


def _normalise_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/")


@dataclass(frozen=True, slots=True)
class CleanupCandidate:
    path: str
    recommended_action: str
    reason: str
    risk: str
    git_status: str
    size_bytes: int
    requires_approval: bool
    duplicate_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.recommended_action not in _ALLOWED_ACTIONS:
            raise ValueError("invalid cleanup action")
        if self.risk not in _ALLOWED_RISKS:
            raise ValueError("invalid cleanup risk")
        if not self.path:
            raise ValueError("cleanup candidate path is required")

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["duplicate_paths"] = list(
            self.duplicate_paths
        )
        return payload


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    schema_version: int
    plan_id: str
    source_inventory_id: str
    candidate_count: int
    keep_count: int
    review_count: int
    delete_count: int
    archive_count: int
    reclaimable_bytes: int
    candidates: tuple[CleanupCandidate, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "source_inventory_id": self.source_inventory_id,
            "candidate_count": self.candidate_count,
            "keep_count": self.keep_count,
            "review_count": self.review_count,
            "delete_count": self.delete_count,
            "archive_count": self.archive_count,
            "reclaimable_bytes": self.reclaimable_bytes,
            "candidates": [
                item.to_dict()
                for item in self.candidates
            ],
            "warnings": list(self.warnings),
        }


class RepositoryCleanupPlanner:
    """Create a deterministic cleanup plan without changing files."""

    DEFAULT_LARGE_FILE_BYTES = 50 * 1024 * 1024

    CACHE_PARTS = frozenset(
        {
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        }
    )
    BACKUP_SUFFIXES = (
        ".bak",
        ".orig",
        ".rej",
        ".tmp",
        ".temp",
        ".old",
    )
    ARCHIVE_SUFFIXES = (
        ".zip",
        ".patch",
        ".diff",
        ".log",
    )
    SCRIPT_PREFIXES = (
        "install_",
        "fix_",
        "rollback_",
        "apply_",
        "restore_",
    )
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
        *,
        large_file_bytes: int | None = None,
        protected_roots: Iterable[str] = (),
    ) -> None:
        self.large_file_bytes = (
            self.DEFAULT_LARGE_FILE_BYTES
            if large_file_bytes is None
            else int(large_file_bytes)
        )
        if self.large_file_bytes <= 0:
            raise ValueError(
                "large_file_bytes must be positive"
            )

        self.protected_roots = frozenset(
            self.PROTECTED_ROOTS
            | {
                str(item).strip()
                for item in protected_roots
                if str(item).strip()
            }
        )

    @staticmethod
    def _duplicate_index(
        inventory: RepositoryInventory,
    ) -> dict[str, tuple[str, ...]]:
        index: dict[str, tuple[str, ...]] = {}
        for group in inventory.duplicates:
            for path in group.paths:
                index[path] = tuple(
                    item
                    for item in group.paths
                    if item != path
                )
        return index

    def _is_protected(self, path: str) -> bool:
        first = path.split("/", 1)[0]
        return first in self.protected_roots

    @classmethod
    def _is_cache(cls, path: str) -> bool:
        parts = set(path.split("/"))
        name = path.rsplit("/", 1)[-1].casefold()
        return (
            bool(parts & cls.CACHE_PARTS)
            or name.endswith(".pyc")
            or name.endswith(".pyo")
        )

    @classmethod
    def _is_backup(cls, path: str) -> bool:
        name = path.rsplit("/", 1)[-1].casefold()
        return (
            name.endswith(cls.BACKUP_SUFFIXES)
            or ".bak_" in name
            or name.endswith("~")
        )

    @classmethod
    def _is_single_use_script(cls, path: str) -> bool:
        name = path.rsplit("/", 1)[-1].casefold()
        return (
            name.endswith(".py")
            and name.startswith(cls.SCRIPT_PREFIXES)
        )

    @classmethod
    def _is_archive_artifact(cls, path: str) -> bool:
        name = path.rsplit("/", 1)[-1].casefold()
        return name.endswith(cls.ARCHIVE_SUFFIXES)

    def _classify(
        self,
        file: RepositoryFile,
        duplicate_paths: tuple[str, ...],
    ) -> CleanupCandidate:
        path = _normalise_path(file.relative_path)
        status = file.status.casefold()

        if status == "tracked":
            return CleanupCandidate(
                path=path,
                recommended_action="keep",
                reason="Git-tracked files are protected by default.",
                risk="high",
                git_status=status,
                size_bytes=file.size_bytes,
                requires_approval=False,
                duplicate_paths=duplicate_paths,
            )

        if self._is_protected(path):
            return CleanupCandidate(
                path=path,
                recommended_action="keep",
                reason="Path is inside a protected project root.",
                risk="high",
                git_status=status,
                size_bytes=file.size_bytes,
                requires_approval=False,
                duplicate_paths=duplicate_paths,
            )

        if self._is_cache(path):
            return CleanupCandidate(
                path=path,
                recommended_action="delete",
                reason="Regenerable cache or compiled Python file.",
                risk="low",
                git_status=status,
                size_bytes=file.size_bytes,
                requires_approval=True,
                duplicate_paths=duplicate_paths,
            )

        if self._is_backup(path):
            return CleanupCandidate(
                path=path,
                recommended_action="delete",
                reason="Untracked or ignored backup artifact.",
                risk="low",
                git_status=status,
                size_bytes=file.size_bytes,
                requires_approval=True,
                duplicate_paths=duplicate_paths,
            )

        if self._is_single_use_script(path):
            return CleanupCandidate(
                path=path,
                recommended_action="archive",
                reason="Possible single-use repair or installer script.",
                risk="medium",
                git_status=status,
                size_bytes=file.size_bytes,
                requires_approval=True,
                duplicate_paths=duplicate_paths,
            )

        if self._is_archive_artifact(path):
            return CleanupCandidate(
                path=path,
                recommended_action="archive",
                reason="Generated archive, patch, diff, or log artifact.",
                risk="medium",
                git_status=status,
                size_bytes=file.size_bytes,
                requires_approval=True,
                duplicate_paths=duplicate_paths,
            )

        if duplicate_paths:
            return CleanupCandidate(
                path=path,
                recommended_action="review",
                reason="Content duplicates another repository file.",
                risk="medium",
                git_status=status,
                size_bytes=file.size_bytes,
                requires_approval=True,
                duplicate_paths=duplicate_paths,
            )

        if file.size_bytes >= self.large_file_bytes:
            return CleanupCandidate(
                path=path,
                recommended_action="review",
                reason="File exceeds the configured large-file threshold.",
                risk="medium",
                git_status=status,
                size_bytes=file.size_bytes,
                requires_approval=True,
            )

        return CleanupCandidate(
            path=path,
            recommended_action="keep",
            reason="No safe cleanup rule matched.",
            risk="high",
            git_status=status,
            size_bytes=file.size_bytes,
            requires_approval=False,
        )

    def build(
        self,
        inventory: RepositoryInventory,
    ) -> CleanupPlan:
        duplicate_index = self._duplicate_index(inventory)
        candidates = tuple(
            sorted(
                (
                    self._classify(
                        file,
                        duplicate_index.get(
                            file.relative_path,
                            (),
                        ),
                    )
                    for file in inventory.files
                ),
                key=lambda item: (
                    item.recommended_action,
                    item.path,
                ),
            )
        )

        counts = {
            action: sum(
                item.recommended_action == action
                for item in candidates
            )
            for action in _ALLOWED_ACTIONS
        }
        reclaimable_bytes = sum(
            item.size_bytes
            for item in candidates
            if item.recommended_action == "delete"
        )
        warnings = tuple(inventory.warnings)

        identity = {
            "schema_version": _SCHEMA_VERSION,
            "source_inventory_id": inventory.inventory_id,
            "candidates": [
                item.to_dict()
                for item in candidates
            ],
            "warnings": list(warnings),
        }
        plan_id = (
            "rcp1-"
            + _sha256_text(
                _canonical_json(identity)
            )[:20]
        )

        return CleanupPlan(
            schema_version=_SCHEMA_VERSION,
            plan_id=plan_id,
            source_inventory_id=inventory.inventory_id,
            candidate_count=len(candidates),
            keep_count=counts["keep"],
            review_count=counts["review"],
            delete_count=counts["delete"],
            archive_count=counts["archive"],
            reclaimable_bytes=reclaimable_bytes,
            candidates=candidates,
            warnings=warnings,
        )

    @staticmethod
    def _inventory_from_mapping(
        payload: Mapping[str, object],
    ) -> RepositoryInventory:
        files_payload = payload.get("files")
        duplicates_payload = payload.get("duplicates")

        if not isinstance(files_payload, list):
            raise ValueError("inventory files must be a list")
        if not isinstance(duplicates_payload, list):
            raise ValueError(
                "inventory duplicates must be a list"
            )

        from artmach_assistant.core.repository_inventory import (
            DuplicateGroup,
        )

        files = tuple(
            RepositoryFile(
                relative_path=_normalise_path(
                    row.get("relative_path")
                ),
                size_bytes=max(
                    0,
                    int(row.get("size_bytes", 0) or 0),
                ),
                status=str(
                    row.get("status", "other")
                ).strip().casefold(),
                sha256=str(row.get("sha256", "")).strip(),
            )
            for row in files_payload
            if isinstance(row, Mapping)
        )
        duplicates = tuple(
            DuplicateGroup(
                sha256=str(row.get("sha256", "")).strip(),
                size_bytes=max(
                    0,
                    int(row.get("size_bytes", 0) or 0),
                ),
                paths=tuple(
                    _normalise_path(item)
                    for item in row.get("paths", [])
                    if _normalise_path(item)
                ),
            )
            for row in duplicates_payload
            if isinstance(row, Mapping)
            and isinstance(row.get("paths"), list)
        )

        return RepositoryInventory(
            schema_version=int(
                payload.get("schema_version", 1) or 1
            ),
            inventory_id=str(
                payload.get("inventory_id", "")
            ).strip(),
            project_root=str(
                payload.get("project_root", "")
            ).strip(),
            tracked_count=int(
                payload.get("tracked_count", 0) or 0
            ),
            untracked_count=int(
                payload.get("untracked_count", 0) or 0
            ),
            ignored_count=int(
                payload.get("ignored_count", 0) or 0
            ),
            total_size_bytes=int(
                payload.get("total_size_bytes", 0) or 0
            ),
            hashed_file_count=int(
                payload.get("hashed_file_count", 0) or 0
            ),
            files=files,
            duplicates=duplicates,
            warnings=tuple(
                str(item)
                for item in payload.get("warnings", [])
                if str(item)
            ),
        )

    def build_from_file(
        self,
        inventory_path: str | Path,
    ) -> CleanupPlan:
        path = (
            Path(inventory_path)
            .expanduser()
            .resolve(strict=False)
        )
        payload = json.loads(
            path.read_text(encoding="utf-8-sig")
        )
        if not isinstance(payload, Mapping):
            raise ValueError(
                "repository inventory must be an object"
            )
        return self.build(
            self._inventory_from_mapping(payload)
        )

    def write(
        self,
        inventory: RepositoryInventory,
        output_path: str | Path,
    ) -> CleanupPlan:
        output = (
            Path(output_path).expanduser().resolve(strict=False)
        )
        if output.suffix.casefold() != ".json":
            raise ValueError(
                "cleanup plan output must be JSON"
            )

        plan = self.build(inventory)
        _atomic_write_json(output, plan.to_dict())
        return plan
