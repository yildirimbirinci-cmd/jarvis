from pathlib import Path

import pytest

from artmach_assistant.core.filesystem_tool_service import FileSystemToolError, FileSystemToolService


def test_lists_directories_before_files_and_hides_dot_entries(tmp_path: Path) -> None:
    (tmp_path / "B_folder").mkdir()
    (tmp_path / "a.txt").write_text("abc", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    service = FileSystemToolService([tmp_path])

    rows = service.list_directory(tmp_path)

    assert [row.name for row in rows] == ["B_folder", "a.txt"]
    assert rows[0].is_directory is True
    assert rows[1].size == 3


def test_rejects_paths_outside_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    service = FileSystemToolService([allowed])

    with pytest.raises(FileSystemToolError, match="dışında"):
        service.list_directory(outside)


def test_create_copy_move_and_rename(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("payload", encoding="utf-8")
    service = FileSystemToolService([root])

    folder = service.create_directory(root, "target").destination
    copied = service.copy(source, folder).destination
    renamed = service.rename(copied, "renamed.txt").destination
    moved = service.move(renamed, root, new_name="final.txt").destination

    assert folder.is_dir()
    assert moved.read_text(encoding="utf-8") == "payload"
    assert not renamed.exists()


def test_rejects_unsafe_leaf_names(tmp_path: Path) -> None:
    service = FileSystemToolService([tmp_path])

    with pytest.raises(FileSystemToolError, match="güvenli değil"):
        service.create_directory(tmp_path, "../escape")
