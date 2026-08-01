import json
from pathlib import Path

from artmach_assistant.core.snapshot_manager import SnapshotManager


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.invalidated = False

    def require_root(self) -> Path:
        return self.root

    def invalidate_index(self) -> None:
        self.invalidated = True


class BrokenText:
    def __str__(self) -> str:
        raise RuntimeError("broken")


def test_create_survives_broken_note_string(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1", encoding="utf-8")
    manager = SnapshotManager(Workspace(tmp_path))
    info = manager.create(BrokenText())
    assert info.files == 1
    assert info.note == "<BrokenText>"


def test_list_skips_excessive_file_count(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    manager = SnapshotManager(workspace)
    folder = manager._root() / "20260101_000000_000000"
    folder.mkdir(parents=True)
    (folder / "snapshot.json").write_text(json.dumps({
        "name": folder.name,
        "created_at": "now",
        "files": 10**9,
        "note": "bad",
    }), encoding="utf-8")
    assert manager.list() == []
