from pathlib import Path

import pytest

from artmach_assistant.core.desktop_folder_service import DesktopFolderError, DesktopFolderService


def test_lists_only_visible_real_directories(tmp_path: Path) -> None:
    (tmp_path / "Jarvis_yedek").mkdir()
    (tmp_path / "test_jarvis").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    service = DesktopFolderService(tmp_path)

    folders = service.list_folders()

    assert [item.name for item in folders] == ["Jarvis_yedek", "test_jarvis"]
    assert [item.index for item in folders] == [1, 2]


def test_selects_folder_by_number_ordinal_and_name(tmp_path: Path) -> None:
    (tmp_path / "Alpha").mkdir()
    (tmp_path / "Jarvis_yedek").mkdir()
    service = DesktopFolderService(tmp_path)
    folders = service.list_folders()

    assert service.select_folder("2", folders).name == "Jarvis_yedek"
    assert service.select_folder("ikinci klasör", folders).name == "Jarvis_yedek"
    assert service.select_folder("Jarvis_yedek klasörünü kullan", folders).name == "Jarvis_yedek"


def test_rejects_invalid_selection(tmp_path: Path) -> None:
    (tmp_path / "Alpha").mkdir()
    service = DesktopFolderService(tmp_path)

    with pytest.raises(DesktopFolderError):
        service.select_folder("dokuzuncu", service.list_folders())


def test_serialization_preserves_order(tmp_path: Path) -> None:
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    service = DesktopFolderService(tmp_path)
    folders = service.list_folders()

    restored = service.deserialize(service.serialize(folders))

    assert [item.name for item in restored] == ["A", "B"]
    assert [item.index for item in restored] == [1, 2]
