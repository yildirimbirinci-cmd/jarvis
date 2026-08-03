from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from artmach_assistant.core.health_aware_repository_maintenance import (
    HealthAwareRepositoryMaintenanceRuntime,
)


def create_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=project,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=project,
        check=True,
    )
    (project / "tracked.txt").write_text(
        "tracked",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "tracked.txt"],
        cwd=project,
        check=True,
    )
    return project


def test_read_only_run_writes_before_health(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    backup = project / "module.py.bak"
    backup.write_text("backup", encoding="utf-8")

    result = HealthAwareRepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    ).run()

    assert result.status == "planned"
    assert backup.exists()
    assert result.health_before < 100
    assert result.health_after == result.health_before
    assert result.health_delta == 0
    assert Path(result.health_before_path).is_file()
    assert result.health_after_path == ""


def test_cleanup_recalculates_after_health(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    backup = project / "module.py.bak"
    backup.write_text("backup", encoding="utf-8")

    result = HealthAwareRepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    ).run(
        allow_cleanup=True,
        approved_paths=["module.py.bak"],
    )

    assert result.status == "completed"
    assert not backup.exists()
    assert result.health_after > result.health_before
    assert result.health_delta > 0
    assert result.health_trend == "improving"
    assert result.reclaimable_bytes_after == 0
    assert Path(result.health_after_path).is_file()


def test_approval_requires_permission(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)

    with pytest.raises(PermissionError):
        HealthAwareRepositoryMaintenanceRuntime(
            project,
            tmp_path / "artifacts",
        ).run(
            approved_paths=["module.py.bak"],
        )


def test_allow_without_paths_awaits_approval(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)

    result = HealthAwareRepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    ).run(allow_cleanup=True)

    assert result.status == "awaiting_approval"
    assert result.execution_path == ""
    assert result.health_delta == 0


def test_tracked_file_failure_keeps_health_stable(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)

    result = HealthAwareRepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    ).run(
        allow_cleanup=True,
        approved_paths=["tracked.txt"],
    )

    assert result.status == "failed"
    assert (project / "tracked.txt").exists()
    assert result.health_after == result.health_before


def test_writes_and_loads_state(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    runtime = HealthAwareRepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    )

    created = runtime.run()
    loaded = runtime.load_last_result()

    assert loaded == created
    payload = json.loads(
        runtime.state_path.read_text(encoding="utf-8")
    )
    assert payload["health_before"] == created.health_before
