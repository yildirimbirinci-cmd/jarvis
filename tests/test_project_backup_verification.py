from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.project_backup_service import ProjectBackupService


def _create_backup(tmp_path: Path):
    source = tmp_path / "project"
    target = tmp_path / "backups"
    source.mkdir()
    (source / "a.py").write_text("value = 1\n", encoding="utf-8")
    return ProjectBackupService().create_backup(source, target)


def test_created_backup_is_verified(tmp_path: Path) -> None:
    result = _create_backup(tmp_path)
    assert result.verified is True
    verification = ProjectBackupService().verify_backup(result.backup_path)
    assert verification.success is True
    assert verification.checked_files == 1


def test_verification_detects_changed_file(tmp_path: Path) -> None:
    result = _create_backup(tmp_path)
    (result.backup_path / "a.py").write_text("value = 2\n", encoding="utf-8")
    verification = ProjectBackupService().verify_backup(result.backup_path)
    assert verification.success is False
    assert verification.changed_files == ("a.py",)


def test_verification_detects_missing_and_unexpected_files(tmp_path: Path) -> None:
    result = _create_backup(tmp_path)
    (result.backup_path / "a.py").unlink()
    (result.backup_path / "extra.txt").write_text("extra", encoding="utf-8")
    verification = ProjectBackupService().verify_backup(result.backup_path)
    assert verification.missing_files == ("a.py",)
    assert verification.unexpected_files == ("extra.txt",)


def test_verification_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    root = tmp_path / "backup"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({
        "version": 1,
        "files": [{"path": "../outside.txt", "size": 0, "sha256": ""}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="güvenli olmayan"):
        ProjectBackupService().verify_backup(root)


def test_backup_skips_symbolic_links(tmp_path: Path) -> None:
    source = tmp_path / "project"
    target = tmp_path / "backups"
    outside = tmp_path / "outside.txt"
    source.mkdir()
    outside.write_text("secret", encoding="utf-8")
    link = source / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Bu ortam sembolik bağlantı oluşturmaya izin vermiyor.")
    result = ProjectBackupService().create_backup(source, target)
    assert not (result.backup_path / "outside-link.txt").exists()
