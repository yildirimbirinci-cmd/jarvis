from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import sys
import types

workspace_stub = types.ModuleType("artmach_assistant.core.workspace")
class WorkspaceError(RuntimeError):
    pass
class WorkspaceService:
    pass
workspace_stub.WorkspaceError = WorkspaceError
workspace_stub.WorkspaceService = WorkspaceService
sys.modules["artmach_assistant.core.workspace"] = workspace_stub

from artmach_assistant.core.build_manager import BuildManager, BuildProfile
from artmach_assistant.core.edit_manager import EditManager, EditProposal, ProposedFileChange


class MinimalWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.invalidations = 0

    def require_root(self) -> Path:
        return self.root

    def safe_path(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        target.relative_to(self.root)
        return target

    def read_text(self, relative_path: str, *, max_chars: int) -> str:
        return self.safe_path(relative_path).read_text(encoding="utf-8")[:max_chars]

    def invalidate_index(self) -> None:
        self.invalidations += 1


def test_edit_apply_restores_original_files_when_later_replace_fails(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("first = 1\n", encoding="utf-8")
    second.write_text("second = 1\n", encoding="utf-8")

    workspace = MinimalWorkspace(tmp_path)
    manager = EditManager(workspace)  # type: ignore[arg-type]
    manager.pending = EditProposal(
        "atomic update",
        [
            ProposedFileChange("first.py", "test", "first = 1\n", "first = 2\n", True),
            ProposedFileChange("second.py", "test", "second = 1\n", "second = 2\n", True),
        ],
    )

    real_replace = os.replace
    project_replaces = 0

    def fail_second_project_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        nonlocal project_replaces
        if str(dst).endswith(("first.py", "second.py")):
            project_replaces += 1
            if project_replaces == 2:
                raise OSError("simulated replace failure")
        real_replace(src, dst)

    with patch("artmach_assistant.core.edit_manager.os.replace", side_effect=fail_second_project_replace):
        with pytest.raises(WorkspaceError, match="geri alındı"):
            manager.apply()

    assert first.read_text(encoding="utf-8") == "first = 1\n"
    assert second.read_text(encoding="utf-8") == "second = 1\n"
    assert manager.pending is not None
    assert workspace.invalidations == 0

    checkpoints = list((tmp_path / ".artmach_assistant" / "checkpoints").iterdir())
    assert len(checkpoints) == 1
    state = json.loads((checkpoints[0] / "state.json").read_text(encoding="utf-8"))
    assert state["state"] == "rolled_back"


def test_edit_apply_marks_checkpoint_applied_only_after_success(tmp_path: Path) -> None:
    target = tmp_path / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")
    workspace = MinimalWorkspace(tmp_path)
    manager = EditManager(workspace)  # type: ignore[arg-type]
    manager.pending = EditProposal(
        "safe update",
        [ProposedFileChange("sample.py", "test", "value = 1\n", "value = 2\n", True)],
    )

    manager.apply()

    checkpoint = next((tmp_path / ".artmach_assistant" / "checkpoints").iterdir())
    state = json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))
    assert state == {"state": "applied"}
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert manager.pending is None
    assert workspace.invalidations == 1


def test_build_manager_rejects_forged_free_form_profile(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
    workspace = MinimalWorkspace(tmp_path)
    manager = BuildManager(workspace)  # type: ignore[arg-type]
    forged = BuildProfile("Arbitrary", ["python", "-c", "print('unsafe')"], "not detected")

    with patch("artmach_assistant.core.build_manager.subprocess.run") as run:
        with pytest.raises(WorkspaceError, match="önceden tanımlı"):
            manager.run(forged)
        run.assert_not_called()
