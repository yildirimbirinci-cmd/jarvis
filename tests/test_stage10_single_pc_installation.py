from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from artmach_assistant.core.deployment_layout import (
    DeploymentPaths,
    export_persistent_data,
    import_persistent_data,
)
from artmach_assistant.core.final_release import (
    FinalReleaseError,
    build_final_release,
    install_release,
    restore_application_backup,
    uninstall_release,
)


def _project(root: Path, value: int = 1) -> Path:
    project = root / "artmach_assistant"
    for directory in ("core", "indexing", "tests", "tools"):
        (project / directory).mkdir(parents=True, exist_ok=True)
    for name in ("app.py", "__main__.py", "config.py"):
        (project / name).write_text(f"VALUE = {value}\n", encoding="utf-8")
    (project / "core" / "feature.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
    return project


def _acceptance(path: Path) -> Path:
    path.write_text(
        json.dumps({
            "run_id": "STAGE10-READY",
            "profile": "full",
            "ready": True,
            "cancelled": False,
            "checks": [{"name": "all", "required": True, "state": "passed"}],
        }),
        encoding="utf-8",
    )
    return path


def _release(tmp_path: Path, label: str, value: int) -> Path:
    project = _project(tmp_path / f"source-{label}", value=value)
    report = _acceptance(tmp_path / f"acceptance-{label}.json")
    result = build_final_release(project, report, tmp_path / f"out-{label}", version=f"1.0.{value}")
    release_root = tmp_path / f"release-{label}"
    with zipfile.ZipFile(result["source_zip"]) as archive:
        archive.extractall(release_root)
    return release_root


def test_update_preserves_persistent_config_engineering_and_memory(tmp_path: Path) -> None:
    data_root = tmp_path / "persistent"
    destination = tmp_path / "program"
    first = _release(tmp_path, "first", 1)
    second = _release(tmp_path, "second", 2)

    install_release(first, destination, data_root=data_root)
    paths = DeploymentPaths.resolve(destination / "artmach_assistant", data_root)
    paths.ensure_persistent_tree()
    (paths.config_root / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
    (paths.engineering_root / "state.json").write_text('{"stage":"safe"}', encoding="utf-8")
    (paths.local_memory_root / "learning.json").write_text('{"lesson":"keep"}', encoding="utf-8")

    result = install_release(second, destination, data_root=data_root)

    assert (destination / "artmach_assistant" / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (paths.config_root / "settings.json").read_text(encoding="utf-8") == '{"theme":"dark"}'
    assert (paths.engineering_root / "state.json").read_text(encoding="utf-8") == '{"stage":"safe"}'
    assert (paths.local_memory_root / "learning.json").read_text(encoding="utf-8") == '{"lesson":"keep"}'
    assert Path(result["persistent_data_root"]) == data_root.resolve()
    record = json.loads((destination / "INSTALLATION.json").read_text(encoding="utf-8"))
    assert Path(record["persistent_data_root"]) == data_root.resolve()


def test_failed_update_restores_old_application_and_keeps_persistent_data(tmp_path: Path) -> None:
    data_root = tmp_path / "persistent"
    destination = tmp_path / "program"
    first = _release(tmp_path, "first", 1)
    second = _release(tmp_path, "second", 2)
    install_release(first, destination, data_root=data_root)
    paths = DeploymentPaths.resolve(destination / "artmach_assistant", data_root)
    paths.ensure_persistent_tree()
    marker = paths.local_memory_root / "marker.txt"
    marker.write_text("persistent", encoding="utf-8")

    with pytest.raises(FinalReleaseError, match="derleme"):
        install_release(second, destination, data_root=data_root, compile_python=tmp_path / "missing-python")

    assert (destination / "artmach_assistant" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert marker.read_text(encoding="utf-8") == "persistent"


def test_explicit_application_rollback_does_not_touch_persistent_data(tmp_path: Path) -> None:
    data_root = tmp_path / "persistent"
    destination = tmp_path / "program"
    first = _release(tmp_path, "first", 1)
    second = _release(tmp_path, "second", 2)
    install_release(first, destination, data_root=data_root)
    result = install_release(second, destination, data_root=data_root)
    marker = data_root / "memory" / "local" / "marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("keep", encoding="utf-8")

    restore_application_backup(result["backup"], destination)

    assert (destination / "artmach_assistant" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert marker.read_text(encoding="utf-8") == "keep"


def test_uninstall_preserves_persistent_data_by_default(tmp_path: Path) -> None:
    data_root = tmp_path / "persistent"
    destination = tmp_path / "program"
    release = _release(tmp_path, "one", 1)
    install_release(release, destination, data_root=data_root)
    marker = data_root / "memory" / "local" / "marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("keep", encoding="utf-8")

    result = uninstall_release(destination, data_root=data_root)

    assert not (destination / "artmach_assistant").exists()
    assert marker.read_text(encoding="utf-8") == "keep"
    assert result["persistent_data_preserved"] == "true"
    assert result["backup"]


def test_uninstall_purges_persistent_data_only_when_explicit(tmp_path: Path) -> None:
    data_root = tmp_path / "persistent"
    destination = tmp_path / "program"
    release = _release(tmp_path, "one", 1)
    install_release(release, destination, data_root=data_root)
    (data_root / "memory" / "local").mkdir(parents=True, exist_ok=True)
    (data_root / "memory" / "local" / "marker.txt").write_text("remove", encoding="utf-8")

    result = uninstall_release(destination, data_root=data_root, purge_persistent_data=True)

    assert not data_root.exists()
    assert result["persistent_data_preserved"] == "false"


def test_pc_or_disk_migration_roundtrip_preserves_persistent_state(tmp_path: Path) -> None:
    source = DeploymentPaths.resolve(tmp_path / "app-a", tmp_path / "data-a")
    source.ensure_persistent_tree()
    (source.config_root / "config.json").write_text('{"ok":true}', encoding="utf-8")
    (source.engineering_root / "session.json").write_text('{"state":"ready"}', encoding="utf-8")
    (source.local_memory_root / "learning.json").write_text('{"lesson":"portable"}', encoding="utf-8")
    bundle = export_persistent_data(source, tmp_path / "echo-migration.zip")

    destination = DeploymentPaths.resolve(tmp_path / "app-b", tmp_path / "data-b")
    import_persistent_data(destination, bundle)

    assert (destination.config_root / "config.json").read_text(encoding="utf-8") == '{"ok":true}'
    assert (destination.engineering_root / "session.json").read_text(encoding="utf-8") == '{"state":"ready"}'
    assert (destination.local_memory_root / "learning.json").read_text(encoding="utf-8") == '{"lesson":"portable"}'
