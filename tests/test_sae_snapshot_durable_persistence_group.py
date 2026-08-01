from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from artmach_assistant.core.self_awareness import SelfAwarenessEngine


class WorkspaceError(RuntimeError):
    pass


class WorkspaceService:
    def __init__(self) -> None:
        self._root: Path | None = None

    def set_root(self, root: Path) -> None:
        self._root = root.resolve()

    def require_root(self) -> Path:
        if self._root is None:
            raise WorkspaceError("workspace missing")
        return self._root

    def invalidate_index(self) -> None:
        return None


_workspace_module = types.ModuleType("artmach_assistant.core.workspace")
_workspace_module.WorkspaceError = WorkspaceError
_workspace_module.WorkspaceService = WorkspaceService
sys.modules["artmach_assistant.core.workspace"] = _workspace_module

from artmach_assistant.core.snapshot_manager import SnapshotManager


def test_sae_atomic_json_preserves_existing_file_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "runtime_state.json"
    target.write_text('{"status":"healthy"}', encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("artmach_assistant.core.store_validation.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        SelfAwarenessEngine._atomic_json_write(target, {"status": "broken"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"status": "healthy"}
    assert not list(tmp_path.glob("*.tmp"))


def test_sae_atomic_json_rejects_non_finite_values_without_touching_file(tmp_path: Path) -> None:
    target = tmp_path / "self_index.json"
    target.write_text('{"generation":1}', encoding="utf-8")

    with pytest.raises(ValueError):
        SelfAwarenessEngine._atomic_json_write(target, {"score": float("nan")})

    assert target.read_text(encoding="utf-8") == '{"generation":1}'


def test_snapshot_creation_cleans_partial_directory_when_metadata_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('ok')\n", encoding="utf-8")
    workspace = WorkspaceService()
    workspace.set_root(project)
    manager = SnapshotManager(workspace)

    def fail_fsync(fd: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr("artmach_assistant.core.snapshot_manager.os.fsync", fail_fsync)

    with pytest.raises(OSError, match="fsync failed"):
        manager.create("durability test")

    snapshot_root = project / ".artmach_assistant" / "snapshots"
    assert snapshot_root.is_dir()
    assert list(snapshot_root.iterdir()) == []
