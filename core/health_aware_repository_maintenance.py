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
from artmach_assistant.core.repository_health import (
    RepositoryHealthEngine,
    RepositoryHealthReport,
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
class HealthAwareMaintenanceResult:
    schema_version: int
    maintenance_id: str
    status: str
    inventory_before_path: str
    cleanup_plan_before_path: str
    health_before_path: str
    execution_path: str
    inventory_after_path: str
    cleanup_plan_after_path: str
    health_after_path: str
    health_before: int
    health_after: int
    health_delta: int
    health_trend: str
    approved_paths: tuple[str, ...]
    reclaimable_bytes_before: int
    reclaimable_bytes_after: int
    reclaimed_bytes: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["approved_paths"] = list(self.approved_paths)
        payload["warnings"] = list(self.warnings)
        return payload


class HealthAwareRepositoryMaintenanceRuntime:
    """Run repository maintenance with before/after health reports."""

    def __init__(
        self,
        project_root: str | Path,
        artifact_root: str | Path,
        *,
        archive_root: str | Path | None = None,
        inventory_service: RepositoryInventoryService | None = None,
        cleanup_planner: RepositoryCleanupPlanner | None = None,
        health_engine: RepositoryHealthEngine | None = None,
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
        self.health_engine = (
            health_engine
            if health_engine is not None
            else RepositoryHealthEngine()
        )

        self.inventory_before_path = (
            self.artifact_root / "repository_inventory_before.json"
        )
        self.cleanup_plan_before_path = (
            self.artifact_root / "repository_cleanup_plan_before.json"
        )
        self.health_before_path = (
            self.artifact_root / "repository_health_before.json"
        )
        self.execution_path = (
            self.artifact_root / "repository_cleanup_execution.json"
        )
        self.inventory_after_path = (
            self.artifact_root / "repository_inventory_after.json"
        )
        self.cleanup_plan_after_path = (
            self.artifact_root / "repository_cleanup_plan_after.json"
        )
        self.health_after_path = (
            self.artifact_root / "repository_health_after.json"
        )
        self.state_path = (
            self.artifact_root
            / "health_aware_repository_maintenance_state.json"
        )

    @staticmethod
    def _normalise_paths(
        paths: Iterable[str],
    ) -> tuple[str, ...]:
        normalised: list[str] = []
        for value in paths:
            path = str(value).strip().replace("\\", "/")
            if path and path not in normalised:
                normalised.append(path)
        return tuple(normalised)

    def _inventory(
        self,
        output: Path,
    ) -> RepositoryInventory:
        inventory = self.inventory_service.build()
        _atomic_write_json(output, inventory.to_dict())
        return inventory

    def _plan(
        self,
        inventory: RepositoryInventory,
        output: Path,
    ) -> CleanupPlan:
        plan = self.cleanup_planner.build(inventory)
        _atomic_write_json(output, plan.to_dict())
        return plan

    def _health(
        self,
        inventory: RepositoryInventory,
        plan: CleanupPlan,
        output: Path,
        *,
        previous_score: int | None = None,
    ) -> RepositoryHealthReport:
        report = self.health_engine.build(
            inventory,
            plan,
            previous_score=previous_score,
        )
        self.health_engine.write(report, output)
        return report

    def _execute(
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
        return execution.status

    def run(
        self,
        *,
        allow_cleanup: bool = False,
        approved_paths: Iterable[str] = (),
    ) -> HealthAwareMaintenanceResult:
        approved = self._normalise_paths(approved_paths)
        if approved and not allow_cleanup:
            raise PermissionError(
                "cleanup approval requires allow_cleanup"
            )

        inventory_before = self._inventory(
            self.inventory_before_path
        )
        plan_before = self._plan(
            inventory_before,
            self.cleanup_plan_before_path,
        )
        health_before = self._health(
            inventory_before,
            plan_before,
            self.health_before_path,
        )

        execution: CleanupExecutionResult | None = None
        inventory_after = inventory_before
        plan_after = plan_before
        health_after = health_before

        if allow_cleanup and approved:
            execution = self._execute(
                plan_before,
                approved,
            )
            inventory_after = self._inventory(
                self.inventory_after_path
            )
            plan_after = self._plan(
                inventory_after,
                self.cleanup_plan_after_path,
            )
            health_after = self._health(
                inventory_after,
                plan_after,
                self.health_after_path,
                previous_score=health_before.health_score,
            )

        warnings = list(inventory_before.warnings)
        warnings.extend(plan_before.warnings)
        warnings.extend(health_before.warnings)
        if execution is not None:
            warnings.extend(execution.warnings)
            warnings.extend(inventory_after.warnings)
            warnings.extend(plan_after.warnings)
            warnings.extend(health_after.warnings)

        identity = {
            "schema_version": _SCHEMA_VERSION,
            "inventory_before_id": inventory_before.inventory_id,
            "cleanup_plan_before_id": plan_before.plan_id,
            "health_before_id": health_before.report_id,
            "execution_id": (
                execution.execution_id
                if execution is not None
                else ""
            ),
            "inventory_after_id": inventory_after.inventory_id,
            "cleanup_plan_after_id": plan_after.plan_id,
            "health_after_id": health_after.report_id,
            "approved_paths": list(approved),
        }
        maintenance_id = (
            "hamr1-"
            + _sha256_text(
                _canonical_json(identity)
            )[:20]
        )

        result = HealthAwareMaintenanceResult(
            schema_version=_SCHEMA_VERSION,
            maintenance_id=maintenance_id,
            status=self._status(
                allow_cleanup=allow_cleanup,
                approved_paths=approved,
                execution=execution,
            ),
            inventory_before_path=str(
                self.inventory_before_path
            ),
            cleanup_plan_before_path=str(
                self.cleanup_plan_before_path
            ),
            health_before_path=str(self.health_before_path),
            execution_path=(
                str(self.execution_path)
                if execution is not None
                else ""
            ),
            inventory_after_path=(
                str(self.inventory_after_path)
                if execution is not None
                else ""
            ),
            cleanup_plan_after_path=(
                str(self.cleanup_plan_after_path)
                if execution is not None
                else ""
            ),
            health_after_path=(
                str(self.health_after_path)
                if execution is not None
                else ""
            ),
            health_before=health_before.health_score,
            health_after=health_after.health_score,
            health_delta=(
                health_after.health_score
                - health_before.health_score
            ),
            health_trend=(
                health_after.trend
                if execution is not None
                else "unknown"
            ),
            approved_paths=approved,
            reclaimable_bytes_before=(
                plan_before.reclaimable_bytes
            ),
            reclaimable_bytes_after=(
                plan_after.reclaimable_bytes
            ),
            reclaimed_bytes=(
                execution.reclaimed_bytes
                if execution is not None
                else 0
            ),
            warnings=tuple(dict.fromkeys(warnings)),
        )
        _atomic_write_json(self.state_path, result.to_dict())
        return result

    def load_last_result(
        self,
    ) -> HealthAwareMaintenanceResult | None:
        if not self.state_path.is_file():
            return None

        payload = json.loads(
            self.state_path.read_text(encoding="utf-8-sig")
        )
        if not isinstance(payload, dict):
            raise ValueError(
                "health-aware maintenance state must be an object"
            )

        return HealthAwareMaintenanceResult(
            schema_version=int(
                payload.get("schema_version", 1) or 1
            ),
            maintenance_id=str(
                payload.get("maintenance_id", "")
            ),
            status=str(payload.get("status", "")),
            inventory_before_path=str(
                payload.get("inventory_before_path", "")
            ),
            cleanup_plan_before_path=str(
                payload.get("cleanup_plan_before_path", "")
            ),
            health_before_path=str(
                payload.get("health_before_path", "")
            ),
            execution_path=str(
                payload.get("execution_path", "")
            ),
            inventory_after_path=str(
                payload.get("inventory_after_path", "")
            ),
            cleanup_plan_after_path=str(
                payload.get("cleanup_plan_after_path", "")
            ),
            health_after_path=str(
                payload.get("health_after_path", "")
            ),
            health_before=int(
                payload.get("health_before", 0) or 0
            ),
            health_after=int(
                payload.get("health_after", 0) or 0
            ),
            health_delta=int(
                payload.get("health_delta", 0) or 0
            ),
            health_trend=str(
                payload.get("health_trend", "unknown")
            ),
            approved_paths=tuple(
                str(item)
                for item in payload.get("approved_paths", [])
                if str(item)
            ),
            reclaimable_bytes_before=max(
                0,
                int(
                    payload.get(
                        "reclaimable_bytes_before",
                        0,
                    )
                    or 0
                ),
            ),
            reclaimable_bytes_after=max(
                0,
                int(
                    payload.get(
                        "reclaimable_bytes_after",
                        0,
                    )
                    or 0
                ),
            ),
            reclaimed_bytes=max(
                0,
                int(
                    payload.get("reclaimed_bytes", 0)
                    or 0
                ),
            ),
            warnings=tuple(
                str(item)
                for item in payload.get("warnings", [])
                if str(item)
            ),
        )
