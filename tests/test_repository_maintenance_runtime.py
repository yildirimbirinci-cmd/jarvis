from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from artmach_assistant.core.repository_maintenance_runtime import (
    RepositoryMaintenanceRuntime,
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
    (project / ".gitignore").write_text(
        "__pycache__/\n",
        encoding="utf-8",
    )
    (project / "tracked.txt").write_text(
        "tracked",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", ".gitignore", "tracked.txt"],
        cwd=project,
        check=True,
    )
    return project


def test_default_run_is_read_only_plan(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    cache = project / "scratch" / "__pycache__"
    cache.mkdir(parents=True)
    source = cache / "sample.pyc"
    source.write_bytes(b"cache")

    result = RepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    ).run()

    assert result.status == "planned"
    assert source.exists()
    assert Path(result.inventory_path).is_file()
    assert Path(result.cleanup_plan_path).is_file()
    assert result.execution_path == ""


def test_cleanup_requires_permission(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)

    with pytest.raises(PermissionError):
        RepositoryMaintenanceRuntime(
            project,
            tmp_path / "artifacts",
        ).run(
            approved_paths=["temp.pyc"],
        )


def test_allow_without_paths_awaits_approval(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)

    result = RepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    ).run(allow_cleanup=True)

    assert result.status == "awaiting_approval"
    assert result.execution_id == ""


def test_executes_only_approved_cleanup(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    source = project / "module.py.bak"
    source.write_text("backup", encoding="utf-8")
    keep = project / "notes.txt"
    keep.write_text("keep", encoding="utf-8")

    result = RepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    ).run(
        allow_cleanup=True,
        approved_paths=["module.py.bak"],
    )

    assert result.status == "completed"
    assert not source.exists()
    assert keep.exists()
    assert result.reclaimed_bytes == len("backup")
    assert Path(result.execution_path).is_file()


def test_archives_approved_artifact(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    source = project / "old.patch"
    source.write_text("patch", encoding="utf-8")
    artifacts = tmp_path / "artifacts"

    result = RepositoryMaintenanceRuntime(
        project,
        artifacts,
    ).run(
        allow_cleanup=True,
        approved_paths=["old.patch"],
    )

    assert result.status == "completed"
    assert not source.exists()
    assert (
        artifacts / "archive" / "old.patch"
    ).read_text(encoding="utf-8") == "patch"


def test_tracked_file_remains_protected(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)

    result = RepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    ).run(
        allow_cleanup=True,
        approved_paths=["tracked.txt"],
    )

    assert result.status == "failed"
    assert (project / "tracked.txt").exists()


def test_writes_and_loads_state(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    runtime = RepositoryMaintenanceRuntime(
        project,
        tmp_path / "artifacts",
    )

    created = runtime.run()
    loaded = runtime.load_last_result()

    assert loaded == created
    payload = json.loads(
        runtime.state_path.read_text(encoding="utf-8")
    )
    assert payload["maintenance_id"] == (
        created.maintenance_id
    )


def test_rejects_project_as_artifact_root(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)

    with pytest.raises(ValueError, match="artifact root"):
        RepositoryMaintenanceRuntime(
            project,
            project,
        )
