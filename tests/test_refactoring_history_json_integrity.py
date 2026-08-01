from __future__ import annotations

import json
from pathlib import Path

import pytest

from artmach_assistant.core.refactoring_transaction_history import (
    RefactoringTransactionHistory,
)
from artmach_assistant.core.workspace import WorkspaceError


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.invalidations = 0

    def require_root(self) -> Path:
        return self.root

    def safe_path(self, path: str) -> Path:
        target = (self.root / path).resolve(strict=False)
        target.relative_to(self.root)
        return target

    def invalidate_index(self) -> None:
        self.invalidations += 1


def make_checkpoint(root: Path, *, manifest_text: str, state_text: str | None = None) -> Path:
    checkpoint = root / ".artmach_assistant" / "checkpoints" / "0001"
    (checkpoint / "before").mkdir(parents=True)
    (checkpoint / "after").mkdir(parents=True)
    (checkpoint / "manifest.json").write_text(manifest_text, encoding="utf-8")
    if state_text is not None:
        (checkpoint / "state.json").write_text(state_text, encoding="utf-8")
    return checkpoint


def test_duplicate_state_key_is_rejected(tmp_path: Path) -> None:
    make_checkpoint(
        tmp_path,
        manifest_text='[{"path": "a.py", "existed": false}]',
        state_text='{"state": "applied", "state": "undone"}',
    )
    history = RefactoringTransactionHistory(FakeWorkspace(tmp_path))

    with pytest.raises(WorkspaceError, match="durumu okunamadı"):
        history.undo()


def test_duplicate_manifest_path_is_rejected_before_writes(tmp_path: Path) -> None:
    checkpoint = make_checkpoint(
        tmp_path,
        manifest_text=json.dumps(
            [
                {"path": "a.py", "existed": False},
                {"path": "a.py", "existed": False},
            ]
        ),
    )
    (checkpoint / "after" / "a.py").write_text("new\n", encoding="utf-8")
    workspace = FakeWorkspace(tmp_path)
    history = RefactoringTransactionHistory(workspace)

    with pytest.raises(WorkspaceError, match="yinelenen dosya yolu"):
        history.undo()

    assert not (tmp_path / "a.py").exists()
    assert workspace.invalidations == 0


@pytest.mark.parametrize("unsafe_path", ["../outside.py", "/absolute.py", "a/../../outside.py"])
def test_unsafe_manifest_path_is_rejected(tmp_path: Path, unsafe_path: str) -> None:
    make_checkpoint(
        tmp_path,
        manifest_text=json.dumps([{"path": unsafe_path, "existed": False}]),
    )
    workspace = FakeWorkspace(tmp_path)
    history = RefactoringTransactionHistory(workspace)

    with pytest.raises(WorkspaceError, match="geçersiz dosya yolu"):
        history.undo()

    assert workspace.invalidations == 0


def test_non_finite_manifest_value_is_rejected(tmp_path: Path) -> None:
    make_checkpoint(
        tmp_path,
        manifest_text='[{"path": "a.py", "existed": NaN}]',
    )
    history = RefactoringTransactionHistory(FakeWorkspace(tmp_path))

    with pytest.raises(WorkspaceError, match="manifesti okunamadı"):
        history.undo()
