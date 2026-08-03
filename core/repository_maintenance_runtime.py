from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from artmach_assistant.core.repository_cleanup_executor import (
    CleanupExecutionResult,
    SafeRepositoryCleanupExecutor,
)
from artmach_assistant.core.repository_cleanup_planner import (
    CleanupPlan,
    RepositoryCleanupPlanner,
)
from artmach_assistant.core.repository_inventory import (
    RepositoryInventory,
    RepositoryInventoryService,
)


_SCHEMA_VERSION = 1


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
class RepositoryMaintenanceResult:
    schema_version: int
    maintenance_id: str
    status: str
    inventory_path: str
    cleanup_plan_path: str
    execution_path: str
    inventory_id: str
    cleanup_plan_id: str
    execution_id: str
    approved_paths: tuple[str, ...]
    reclaimable_bytes: int
    reclaimed_bytes: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["approved_paths"] = list(
            self.approved_paths
        )
        payload["warnings"] = list(self.warnings)
        return payload


class RepositoryMaintenanceRuntime:
    """Coordinate repository inventory, planning and approved cleanup.

    Default execution is read-only. Cleanup occurs only when
    ``allow_cleanup`` is true and approved paths are supplied.
    """

    def __init__(
        self,
        project_root: str | Path,
        artifact_root: str | Path,
        *,
        archive_root: str | Path | None = None,
        inventory_service: RepositoryInventoryService | None = None,
        cleanup_planner: RepositoryCleanupPlanner | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve(strict=False)
        )
        self.artifact_root = (
            Path(artifact_root).expanduser().resolve(strict=False)
        )
        self.archive_root = (
            Path(archive_root).expanduser().resolve(strict=False)
            if archive_root is not None
            else self.artifact_root / "archive"
        )

        if not self.project_root.is_dir():
            raise NotADirectoryError(self.project_root)
        if self.artifact_root == self.project_root:
            raise ValueError(
                "maintenance artifact root must not be project root"
            )
        try:
            self.archive_root.relative_to(self.project_root)
        except ValueError:
            pass
        else:
            raise ValueError(
                "maintenance archive root must be outside project root"
            )

        self.inventory_service = (
            inventory_service
            if inventory_service is not None
            else RepositoryInventoryService(self.project_root)
        )
        self.cleanup_planner = (
            cleanup_planner
            if cleanup_planner is not None
            else RepositoryCleanupPlanner()
        )

        self.inventory_path = (
            self.artifact_root / "repository_inventory.json"
        )
        self.cleanup_plan_path = (
            self.artifact_root / "repository_cleanup_plan.json"
        )
        self.execution_path = (
            self.artifact_root / "repository_cleanup_execution.json"
        )
        self.state_path = (
            self.artifact_root / "repository_maintenance_state.json"
        )

    @staticmethod
    def _normalise_approved_paths(
        paths: Iterable[str],
    ) -> tuple[str, ...]:
        normalised: list[str] = []
        for value in paths:
            path = str(value).strip().replace("\\", "/")
            if path and path not in normalised:
                normalised.append(path)
        return tuple(normalised)

    def _write_inventory(
        self,
    ) -> RepositoryInventory:
        return self.inventory_service.write(
            self.inventory_path
        )

    def _write_cleanup_plan(
        self,
        inventory: RepositoryInventory,
    ) -> CleanupPlan:
        return self.cleanup_planner.write(
            inventory,
            self.cleanup_plan_path,
        )

    def _execute_cleanup(
        self,
        plan: CleanupPlan,
        approved_paths: tuple[str, ...],
    ) -> CleanupExecutionResult:
        executor = SafeRepositoryCleanupExecutor(
            self.project_root,
            self.archive_root,
        )
        result = executor.execute(plan, approved_paths)
        executor.write_result(result, self.execution_path)
        return result

    @staticmethod
    def _status(
        *,
        allow_cleanup: bool,
        approved_paths: tuple[str, ...],
        execution: CleanupExecutionResult | None,
    ) -> str:
        if not allow_cleanup:
            return "planned"
        if not approved_paths:
            return "awaiting_approval"
        if execution is None:
            return "failed"
        if execution.status == "completed":
            return "completed"
        return execution.status

    def run(
        self,
        *,
        allow_cleanup: bool = False,
        approved_paths: Iterable[str] = (),
    ) -> RepositoryMaintenanceResult:
        approved = self._normalise_approved_paths(
            approved_paths
        )
        if approved and not allow_cleanup:
            raise PermissionError(
                "cleanup approval requires allow_cleanup"
            )

        inventory = self._write_inventory()
        plan = self._write_cleanup_plan(inventory)
        execution: CleanupExecutionResult | None = None

        if allow_cleanup and approved:
            execution = self._execute_cleanup(
                plan,
                approved,
            )

        warnings = list(inventory.warnings)
        warnings.extend(plan.warnings)
        if execution is not None:
            warnings.extend(execution.warnings)

        identity = {
            "schema_version": _SCHEMA_VERSION,
            "inventory_id": inventory.inventory_id,
            "cleanup_plan_id": plan.plan_id,
            "execution_id": (
                execution.execution_id
                if execution is not None
                else ""
            ),
            "approved_paths": list(approved),
            "allow_cleanup": allow_cleanup,
        }
        maintenance_id = (
            "rmr1-"
            + _sha256_text(
                _canonical_json(identity)
            )[:20]
        )

        result = RepositoryMaintenanceResult(
            schema_version=_SCHEMA_VERSION,
            maintenance_id=maintenance_id,
            status=self._status(
                allow_cleanup=allow_cleanup,
                approved_paths=approved,
                execution=execution,
            ),
            inventory_path=str(self.inventory_path),
            cleanup_plan_path=str(
                self.cleanup_plan_path
            ),
            execution_path=(
                str(self.execution_path)
                if execution is not None
                else ""
            ),
            inventory_id=inventory.inventory_id,
            cleanup_plan_id=plan.plan_id,
            execution_id=(
                execution.execution_id
                if execution is not None
                else ""
            ),
            approved_paths=approved,
            reclaimable_bytes=plan.reclaimable_bytes,
            reclaimed_bytes=(
                execution.reclaimed_bytes
                if execution is not None
                else 0
            ),
            warnings=tuple(dict.fromkeys(warnings)),
        )
        _atomic_write_json(
            self.state_path,
            result.to_dict(),
        )
        return result

    def load_last_result(
        self,
    ) -> RepositoryMaintenanceResult | None:
        if not self.state_path.is_file():
            return None

        payload = json.loads(
            self.state_path.read_text(
                encoding="utf-8-sig"
            )
        )
        if not isinstance(payload, dict):
            raise ValueError(
                "maintenance state must be an object"
            )

        return RepositoryMaintenanceResult(
            schema_version=int(
                payload.get("schema_version", 1) or 1
            ),
            maintenance_id=str(
                payload.get("maintenance_id", "")
            ),
            status=str(payload.get("status", "")),
            inventory_path=str(
                payload.get("inventory_path", "")
            ),
            cleanup_plan_path=str(
                payload.get("cleanup_plan_path", "")
            ),
            execution_path=str(
                payload.get("execution_path", "")
            ),
            inventory_id=str(
                payload.get("inventory_id", "")
            ),
            cleanup_plan_id=str(
                payload.get("cleanup_plan_id", "")
            ),
            execution_id=str(
                payload.get("execution_id", "")
            ),
            approved_paths=tuple(
                str(item)
                for item in payload.get(
                    "approved_paths",
                    [],
                )
                if str(item)
            ),
            reclaimable_bytes=max(
                0,
                int(
                    payload.get(
                        "reclaimable_bytes",
                        0,
                    )
                    or 0
                ),
            ),
            reclaimed_bytes=max(
                0,
                int(
                    payload.get(
                        "reclaimed_bytes",
                        0,
                    )
                    or 0
                ),
            ),
            warnings=tuple(
                str(item)
                for item in payload.get("warnings", [])
                if str(item)
            ),
        )
