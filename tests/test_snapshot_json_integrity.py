from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.snapshot_manager import SnapshotManager


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.invalidated = 0

    def require_root(self) -> Path:
        return self.root

    def invalidate_index(self) -> None:
        self.invalidated += 1


def test_snapshot_list_ignores_metadata_with_duplicate_keys(tmp_path: Path) -> None:
    manager = SnapshotManager(_Workspace(tmp_path))
    folder = tmp_path / ".artmach_assistant" / "snapshots" / "20260729_120000_000000"
    folder.mkdir(parents=True)
    (folder / "snapshot.json").write_text(
        '{"name":"20260729_120000_000000","files":1,"files":999,"created_at":"now","note":"x"}',
        encoding="utf-8",
    )

    assert manager.list() == []


def test_snapshot_restore_rejects_manifest_with_duplicate_file_key(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    workspace = _Workspace(tmp_path)
    manager = SnapshotManager(workspace)
    snapshot = manager.create("baseline")

    manifest_path = (
        tmp_path
        / ".artmach_assistant"
        / "snapshots"
        / snapshot.name
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = json.dumps(manifest["module.py"], ensure_ascii=False)
    manifest_path.write_text(
        '{"module.py":' + entry + ',"module.py":' + entry + '}',
        encoding="utf-8",
    )
    source.write_text("current = 2\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest geçersiz"):
        manager.restore(snapshot.name)

    assert source.read_text(encoding="utf-8") == "current = 2\n"
    assert workspace.invalidated == 0


def test_snapshot_restore_rejects_non_finite_manifest_number(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    manager = SnapshotManager(_Workspace(tmp_path))
    snapshot = manager.create("baseline")
    manifest_path = (
        tmp_path
        / ".artmach_assistant"
        / "snapshots"
        / snapshot.name
        / "manifest.json"
    )
    raw = manifest_path.read_text(encoding="utf-8").replace('"size": 10', '"size": NaN')
    # Ensure the test remains effective if source length changes.
    if raw == manifest_path.read_text(encoding="utf-8"):
        raw = raw.replace('"size": 10', '"size": NaN')
        raw = raw.replace('"size": 11', '"size": NaN')
    manifest_path.write_text(raw, encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest geçersiz"):
        manager.restore(snapshot.name)
