from pathlib import Path

import pytest

from artmach_assistant.core.filesystem_tool_service import FileSystemToolError, FileSystemToolService


def test_undo_copy_removes_only_created_copy(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("payload", encoding="utf-8")
    target_dir = root / "target"
    target_dir.mkdir()
    service = FileSystemToolService([root])

    copied = service.copy(source, target_dir).destination
    result = service.undo_last()

    assert source.exists()
    assert not copied.exists()
    assert result.action == "undo_copy"
    assert service.history_size == 0


def test_undo_move_restores_original_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("payload", encoding="utf-8")
    target_dir = root / "target"
    target_dir.mkdir()
    service = FileSystemToolService([root])

    moved = service.move(source, target_dir).destination
    restored = service.undo_last().destination

    assert restored == source
    assert source.read_text(encoding="utf-8") == "payload"
    assert not moved.exists()


def test_undo_rename_restores_original_name(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "before.txt"
    source.write_text("x", encoding="utf-8")
    service = FileSystemToolService([root])

    renamed = service.rename(source, "after.txt").destination
    restored = service.undo_last().destination

    assert restored == source
    assert source.exists()
    assert not renamed.exists()


def test_undo_created_directory_requires_empty_directory(tmp_path: Path) -> None:
    service = FileSystemToolService([tmp_path])
    created = service.create_directory(tmp_path, "created").destination
    (created / "data.txt").write_text("x", encoding="utf-8")

    with pytest.raises(FileSystemToolError, match="boş olmadığı"):
        service.undo_last()

    assert created.exists()
    assert service.history_size == 1


def test_undo_without_history_is_rejected(tmp_path: Path) -> None:
    service = FileSystemToolService([tmp_path])

    with pytest.raises(FileSystemToolError, match="Geri alınabilecek"):
        service.undo_last()
