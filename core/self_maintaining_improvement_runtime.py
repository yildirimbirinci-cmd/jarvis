from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from artmach_assistant.core.autonomous_improvement_loop import (
    ImprovementLoopResult,
    ImprovementTrigger,
)
from artmach_assistant.core.repository_maintenance_runtime import (
    RepositoryMaintenanceResult,
    RepositoryMaintenanceRuntime,
)
from artmach_assistant.core.self_improvement_loop_runtime import (
    SelfImprovementLoopRuntime,
)


class SelfMaintainingImprovementRuntime:
    """Run repository maintenance before self-improvement.

    Maintenance is read-only by default. Cleanup requires both
    ``allow_repository_cleanup=True`` and explicit approved paths.
    Maintenance failures are recorded but do not block the existing
    self-improvement runtime.
    """

    def __init__(
        self,
        project_root: str | Path,
        journal_path: str | Path,
        runtime_root: str | Path,
        *,
        candidate_id: str | None = None,
        experiment_result_paths: Sequence[str | Path] = (),
        experiment_changeset_path: str | Path | None = None,
        focused_test_targets: Sequence[str] = (),
        full_test_targets: Sequence[str] = (),
        experiment_timeout_seconds: int = 120,
        enable_repository_maintenance: bool = True,
        allow_repository_cleanup: bool = False,
        approved_cleanup_paths: Sequence[str] = (),
        maintenance_archive_root: str | Path | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve(strict=False)
        )
        self.runtime_root = (
            Path(runtime_root).expanduser().resolve(strict=False)
        )

        self.enable_repository_maintenance = bool(
            enable_repository_maintenance
        )
        self.allow_repository_cleanup = bool(
            allow_repository_cleanup
        )
        self.approved_cleanup_paths = tuple(
            dict.fromkeys(
                str(item).strip().replace("\\", "/")
                for item in approved_cleanup_paths
                if str(item).strip()
            )
        )

        if (
            self.approved_cleanup_paths
            and not self.allow_repository_cleanup
        ):
            raise PermissionError(
                "approved cleanup paths require cleanup permission"
            )

        self.maintenance_root = (
            self.runtime_root / "maintenance"
        )
        self.maintenance_archive_root = (
            Path(maintenance_archive_root)
            .expanduser()
            .resolve(strict=False)
            if maintenance_archive_root is not None
            else self._default_archive_root()
        )
        self.maintenance_preflight_path = (
            self.maintenance_root
            / "repository_maintenance_preflight.json"
        )
        self._maintenance_result: (
            RepositoryMaintenanceResult | None
        ) = None

        self.improvement_runtime = SelfImprovementLoopRuntime(
            self.project_root,
            journal_path,
            self.runtime_root,
            candidate_id=candidate_id,
            experiment_result_paths=experiment_result_paths,
            experiment_changeset_path=(
                experiment_changeset_path
            ),
            focused_test_targets=focused_test_targets,
            full_test_targets=full_test_targets,
            experiment_timeout_seconds=(
                experiment_timeout_seconds
            ),
        )

    def _default_archive_root(self) -> Path:
        try:
            self.runtime_root.relative_to(self.project_root)
        except ValueError:
            return (
                self.runtime_root
                / "repository_maintenance_archive"
            )

        return (
            self.project_root.parent
            / f"{self.project_root.name}_maintenance_archive"
        )

    @property
    def maintenance_result(
        self,
    ) -> RepositoryMaintenanceResult | None:
        return self._maintenance_result

    def _write_preflight(
        self,
        payload: dict[str, object],
    ) -> None:
        self.maintenance_preflight_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.maintenance_preflight_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _run_maintenance(self) -> None:
        if not self.enable_repository_maintenance:
            return

        try:
            result = RepositoryMaintenanceRuntime(
                self.project_root,
                self.maintenance_root,
                archive_root=self.maintenance_archive_root,
            ).run(
                allow_cleanup=self.allow_repository_cleanup,
                approved_paths=self.approved_cleanup_paths,
            )
        except Exception as exc:
            self._write_preflight(
                {
                    "schema_version": 1,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            return

        self._maintenance_result = result
        self._write_preflight(result.to_dict())

    def run(
        self,
        trigger: ImprovementTrigger,
    ) -> ImprovementLoopResult:
        self._run_maintenance()
        return self.improvement_runtime.run(trigger)

    def __getattr__(self, name: str) -> object:
        return getattr(self.improvement_runtime, name)
