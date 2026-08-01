from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.path_permission_service import PathPermissionError
from artmach_assistant.core.project_backup_service import ProjectBackupService


def test_backup_creates_manifest_and_excludes_runtime_files(tmp_path: Path) -> None:
    source = tmp_path / "project"
    target = tmp_path / "backups"
    (source / "core").mkdir(parents=True)
    (source / "core" / "assistant.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "assistant.pyc").write_bytes(b"ignored")

    result = ProjectBackupService().create_backup(source, target, zip_output=True)

    assert result.success
    assert (result.backup_path / "core" / "assistant.py").exists()
    assert not (result.backup_path / "__pycache__").exists()
    assert result.archive_path and result.archive_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["file_count"] == 1
    assert manifest["files"][0]["path"] == "core/assistant.py"


def test_backup_rejects_destination_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    with pytest.raises(PathPermissionError):
        ProjectBackupService().create_backup(source, source / "backup")
