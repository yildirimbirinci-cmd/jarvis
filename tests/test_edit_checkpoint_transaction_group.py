from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

workspace_stub = types.ModuleType("artmach_assistant.core.workspace")

class WorkspaceError(RuntimeError):
    pass

class WorkspaceService:
    pass

workspace_stub.WorkspaceError = WorkspaceError
workspace_stub.WorkspaceService = WorkspaceService
sys.modules["artmach_assistant.core.workspace"] = workspace_stub

from artmach_assistant.core.edit_manager import EditManager, EditProposal, ProposedFileChange


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.invalidated = 0

    def require_root(self) -> Path:
        return self.root

    def safe_path(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        path.relative_to(self.root.resolve())
        return path

    def read_text(self, relative_path: str, max_chars: int = 0) -> str:
        return self.safe_path(relative_path).read_text(encoding="utf-8")

    def invalidate_index(self) -> None:
        self.invalidated += 1


def proposal(*rows: tuple[str, str, str, bool]) -> EditProposal:
    return EditProposal(
        "test",
        [
            ProposedFileChange(path, "test", old, new, existed)
            for path, old, new, existed in rows
        ],
    )


def test_partial_replace_restores_every_original_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.py").write_text("old-a", encoding="utf-8")
    (tmp_path / "b.py").write_text("old-b", encoding="utf-8")
    manager = EditManager(FakeWorkspace(tmp_path))
    manager.pending = proposal(
        ("a.py", "old-a", "new-a", True),
        ("b.py", "old-b", "new-b", True),
    )

    real_replace = os.replace

    def failing_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if target_path == tmp_path / "b.py" and source_path.name.endswith(".artmach-tmp"):
            raise OSError("simulated replace failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(WorkspaceError, match="checkpoint üzerinden geri alındı"):
        manager.apply()

    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "old-a"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "old-b"
    checkpoints = list((tmp_path / ".artmach_assistant" / "checkpoints").glob("[!.]*"))
    assert len(checkpoints) == 1
    state = json.loads((checkpoints[0] / "state.json").read_text(encoding="utf-8"))
    assert state == {"state": "rolled_back"}


def test_checkpoint_preparation_failure_leaves_no_visible_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text("old", encoding="utf-8")
    manager = EditManager(FakeWorkspace(tmp_path))
    manager.pending = proposal(("a.py", "old", "new", True))
    real_write = manager._durable_write_text

    def failing_write(path: Path, content: str) -> None:
        if path.name == "manifest.json":
            raise OSError("disk full")
        real_write(path, content)

    monkeypatch.setattr(manager, "_durable_write_text", failing_write)

    with pytest.raises(WorkspaceError, match="Checkpoint güvenli biçimde hazırlanamadı"):
        manager.apply()

    checkpoint_root = tmp_path / ".artmach_assistant" / "checkpoints"
    assert not list(checkpoint_root.iterdir())
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "old"


def test_successful_apply_marks_checkpoint_applied(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("old", encoding="utf-8")
    workspace = FakeWorkspace(tmp_path)
    manager = EditManager(workspace)
    manager.pending = proposal(("a.py", "old", "new", True))

    result = manager.apply()

    assert "1 dosya güncellendi" in result
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "new"
    checkpoint = next((tmp_path / ".artmach_assistant" / "checkpoints").glob("[!.]*"))
    assert json.loads((checkpoint / "state.json").read_text(encoding="utf-8")) == {
        "state": "applied"
    }
    assert (checkpoint / "before" / "a.py").read_text(encoding="utf-8") == "old"
    assert (checkpoint / "after" / "a.py").read_text(encoding="utf-8") == "new"
    assert workspace.invalidated == 1
    assert manager.pending is None
