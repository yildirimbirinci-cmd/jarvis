from __future__ import annotations

import json
import tempfile
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
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator, RefactoringKind


class FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root
        self.invalidations = 0
    def require_root(self):
        return self.root
    def safe_path(self, path: str):
        target = (self.root / path).resolve(strict=False)
        target.relative_to(self.root)
        return target
    def read_text(self, path: str, max_chars: int):
        return self.safe_path(path).read_text(encoding="utf-8")[:max_chars]
    def invalidate_index(self):
        self.invalidations += 1


class ValidResult:
    issues = ()
    is_valid = True

class Validator:
    def validate(self, root, changes):
        return ValidResult()


def payload(path: str, content: str) -> str:
    return json.dumps({"summary": "test", "files": [{"path": path, "content": content, "reason": "test"}]})


def make_engine(tmp_path: Path, regression=None):
    ws = FakeWorkspace(tmp_path)
    editor = EditManager(ws)
    coordinator = RefactoringCoordinator(editor, Validator())
    return AIRefactoringEngine(coordinator, regression_check=regression), ws


def test_prepare_preview_apply_success(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    engine, _ = make_engine(tmp_path)
    plan = engine.prepare(payload("a.py", "x = 2\n"), kind=RefactoringKind.OTHER)
    preview = engine.preview(plan.plan_id)
    result = engine.apply(preview)
    assert not result.rolled_back
    assert result.regression.safe
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 2\n"


def test_stale_preview_blocks_apply(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    engine, _ = make_engine(tmp_path)
    plan = engine.prepare(payload("a.py", "x = 2\n"), kind="other")
    preview = engine.preview(plan.plan_id)
    target.write_text("external = 1\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="değişen dosyalar"):
        engine.apply(preview)


def test_regression_error_rolls_back(tmp_path: Path):
    from artmach_assistant.core.regression_safety_check import (
        RegressionIssue, RegressionReport, RegressionSeverity
    )
    class FailingRegression:
        def check(self, root, changed_paths, expected_symbols=None):
            return RegressionReport(tuple(changed_paths), (
                RegressionIssue(RegressionSeverity.ERROR, "forced", "forced failure", "a.py"),
            ))
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    engine, _ = make_engine(tmp_path, FailingRegression())
    plan = engine.prepare(payload("a.py", "x = 2\n"), kind="other")
    result = engine.apply(engine.preview(plan.plan_id))
    assert result.rolled_back
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_reject_leaves_file_unchanged(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    engine, _ = make_engine(tmp_path)
    plan = engine.prepare(payload("a.py", "x = 2\n"), kind="other")
    engine.reject(plan.plan_id)
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_undo_redo_exposed_by_engine(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    engine, _ = make_engine(tmp_path)
    plan = engine.prepare(payload("a.py", "x = 2\n"), kind="other")
    engine.apply(engine.preview(plan.plan_id))
    engine.undo()
    assert target.read_text(encoding="utf-8") == "x = 1\n"
    engine.redo()
    assert target.read_text(encoding="utf-8") == "x = 2\n"
