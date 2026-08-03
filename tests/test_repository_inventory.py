from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from artmach_assistant.core.repository_inventory import (
    RepositoryInventoryService,
)


def initialise_repository(project: Path) -> None:
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


def test_builds_status_and_duplicate_inventory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    initialise_repository(project)

    (project / ".gitignore").write_text(
        "ignored.txt\n",
        encoding="utf-8",
    )
    (project / "tracked.txt").write_text(
        "same",
        encoding="utf-8",
    )
    (project / "copy.txt").write_text(
        "same",
        encoding="utf-8",
    )
    (project / "untracked.txt").write_text(
        "different",
        encoding="utf-8",
    )
    (project / "ignored.txt").write_text(
        "ignored",
        encoding="utf-8",
    )

    subprocess.run(
        ["git", "add", ".gitignore", "tracked.txt"],
        cwd=project,
        check=True,
    )

    inventory = RepositoryInventoryService(project).build()
    statuses = {
        item.relative_path: item.status
        for item in inventory.files
    }

    assert statuses["tracked.txt"] == "tracked"
    assert statuses["copy.txt"] == "untracked"
    assert statuses["untracked.txt"] == "untracked"
    assert statuses["ignored.txt"] == "ignored"
    assert any(
        group.paths == ("copy.txt", "tracked.txt")
        for group in inventory.duplicates
    )


def test_is_deterministic(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    initialise_repository(project)
    (project / "a.txt").write_text("alpha", encoding="utf-8")

    service = RepositoryInventoryService(project)

    first = service.build()
    second = service.build()

    assert first.inventory_id == second.inventory_id
    assert first.to_dict() == second.to_dict()


def test_write_is_atomic_json(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    initialise_repository(project)
    (project / "a.txt").write_text("alpha", encoding="utf-8")
    output = tmp_path / "inventory.json"

    inventory = RepositoryInventoryService(project).write(
        output
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["inventory_id"] == inventory.inventory_id
    assert payload["schema_version"] == 1


def test_rejects_non_json_output(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    initialise_repository(project)

    with pytest.raises(ValueError, match="must be JSON"):
        RepositoryInventoryService(project).write(
            tmp_path / "inventory.txt"
        )


def test_does_not_follow_directory_symlink(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")

    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    initialise_repository(project)
    (outside / "secret.txt").write_text(
        "secret",
        encoding="utf-8",
    )

    link = project / "external"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    inventory = RepositoryInventoryService(project).build()

    assert all(
        not item.relative_path.startswith("external/")
        for item in inventory.files
    )


def test_enforces_file_limit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    initialise_repository(project)
    (project / "one.txt").write_text("1", encoding="utf-8")
    (project / "two.txt").write_text("2", encoding="utf-8")

    with pytest.raises(ValueError, match="file limit"):
        RepositoryInventoryService(
            project,
            max_files=1,
        ).build()


def test_skips_hash_for_large_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    initialise_repository(project)
    (project / "large.bin").write_bytes(b"12345")

    inventory = RepositoryInventoryService(
        project,
        max_hash_bytes=4,
    ).build()
    item = next(
        row
        for row in inventory.files
        if row.relative_path == "large.bin"
    )

    assert item.sha256 == ""
    assert inventory.hashed_file_count == 0
