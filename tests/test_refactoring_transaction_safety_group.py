from __future__ import annotations

import json
from pathlib import Path

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

from artmach_assistant.core.ai_refactoring_engine import AIRefactoringEngine
from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator
from artmach_assistant.core.refactoring_transaction_history import RefactoringTransactionHistory


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

    def read_text(self, path: str, max_chars: int) -> str:
        return self.safe_path(path).read_text(encoding="utf-8")[:max_chars]

    def invalidate_index(self) -> None:
        self.invalidations += 1


class ValidResult:
    issues = ()
    is_valid = True


class Validator:
    def validate(self, root, changes):
        return ValidResult()


class ExplodingRegression:
    def check(self, root, changed_paths, expected_symbols=None):
        raise RuntimeError("regression service unavailable")


def payload(files: list[dict[str, str]]) -> str:
    return json.dumps({"summary": "test", "files": files})


def make_engine(root: Path, regression=None):
    workspace = FakeWorkspace(root)
    editor = EditManager(workspace)
    coordinator = RefactoringCoordinator(editor, Validator())
    return AIRefactoringEngine(coordinator, regression_check=regression), workspace


def test_import_check_preflight_does_not_apply_changes(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    engine, _ = make_engine(tmp_path)
    plan = engine.prepare(
        payload([{"path": "a.py", "content": "x = 2\n", "reason": "test"}]),
        kind="other",
    )

    with pytest.raises(WorkspaceError, match="import resolver"):
        engine.apply(engine.preview(plan.plan_id), check_imports=True)

    assert target.read_text(encoding="utf-8") == "x = 1\n"
    assert engine.pending is plan


def test_regression_exception_rolls_back_applied_files(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    engine, _ = make_engine(tmp_path, ExplodingRegression())
    plan = engine.prepare(
        payload([{"path": "a.py", "content": "x = 2\n", "reason": "test"}]),
        kind="other",
    )

    with pytest.raises(WorkspaceError, match="değişiklikler geri alındı"):
        engine.apply(engine.preview(plan.plan_id))

    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_history_restore_failure_does_not_leave_partial_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("a = 1\n", encoding="utf-8")
    second.write_text("b = 1\n", encoding="utf-8")
    workspace = FakeWorkspace(tmp_path)
    editor = EditManager(workspace)
    history = RefactoringTransactionHistory(workspace)
    editor.create_proposal(payload([
        {"path": "a.py", "content": "a = 2\n", "reason": "test"},
        {"path": "b.py", "content": "b = 2\n", "reason": "test"},
    ]))
    editor.apply()

    import artmach_assistant.core.refactoring_transaction_history as history_module

    real_replace = history_module.os.replace
    replacement_count = 0

    def fail_second_history_replace(src, dst):
        nonlocal replacement_count
        if str(src).endswith(".artmach-history"):
            replacement_count += 1
            if replacement_count == 2:
                raise OSError("simulated second replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(history_module.os, "replace", fail_second_history_replace)

    with pytest.raises(WorkspaceError, match="atomik"):
        history.undo()

    assert first.read_text(encoding="utf-8") == "a = 2\n"
    assert second.read_text(encoding="utf-8") == "b = 2\n"
    checkpoint = max((tmp_path / ".artmach_assistant" / "checkpoints").iterdir())
    state = json.loads((checkpoint / "state.json").read_text(encoding="utf-8"))
    assert state["state"] == "applied"
