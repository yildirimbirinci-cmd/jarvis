from __future__ import annotations

import json
import tempfile
import unittest
import sys
import types
from dataclasses import dataclass
from pathlib import Path

# Keep this unit test focused on the coordinator/EditManager transaction.
# WorkspaceService has background-index dependencies that are irrelevant here.
workspace_stub = types.ModuleType("artmach_assistant.core.workspace")
class WorkspaceError(RuntimeError):
    pass
class WorkspaceService:
    pass
workspace_stub.WorkspaceError = WorkspaceError
workspace_stub.WorkspaceService = WorkspaceService
sys.modules["artmach_assistant.core.workspace"] = workspace_stub

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.refactoring_coordinator import (
    RefactoringCoordinator,
    RefactoringKind,
)


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.invalidated = False

    def require_root(self) -> Path:
        return self.root

    def safe_path(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve(strict=False)
        target.relative_to(self.root.resolve(strict=False))
        return target

    def read_text(self, relative_path: str, max_chars: int) -> str:
        return self.safe_path(relative_path).read_text(encoding="utf-8")[:max_chars]

    def invalidate_index(self) -> None:
        self.invalidated = True


@dataclass(frozen=True)
class ValidationResult:
    issues: tuple[object, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str
    line: int | None = None


class Validator:
    def __init__(self, issues: tuple[object, ...] = ()) -> None:
        self.issues = issues

    def validate(self, root: object, changes: object) -> ValidationResult:
        return ValidationResult(self.issues)


class RenameSafety:
    def __init__(self, safe: bool) -> None:
        self.safe = safe
        self.issues = ()


class RefactoringCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.workspace = FakeWorkspace(self.root)
        self.editor = EditManager(self.workspace)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def payload(path: str, content: str) -> str:
        return json.dumps(
            {
                "summary": "test refactor",
                "files": [{"path": path, "content": content, "reason": "test"}],
            }
        )

    def test_prepare_preview_apply_uses_edit_manager_transaction(self) -> None:
        target = self.root / "sample.py"
        target.write_text("value = 1\n", encoding="utf-8")
        coordinator = RefactoringCoordinator(self.editor, Validator())

        plan = coordinator.prepare(
            self.payload("sample.py", "value = 2\n"),
            kind=RefactoringKind.OTHER,
        )

        self.assertIn("+value = 2", coordinator.preview(plan.plan_id))
        result = coordinator.apply(plan.plan_id)
        self.assertIn("1 dosya güncellendi", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
        self.assertIsNone(coordinator.pending)
        self.assertTrue(self.workspace.invalidated)

    def test_failed_patch_validation_clears_both_pending_states(self) -> None:
        coordinator = RefactoringCoordinator(
            self.editor,
            Validator((ValidationIssue("bad.py", "syntax error", 3),)),
        )
        with self.assertRaisesRegex(WorkspaceError, "bad.py:3"):
            coordinator.prepare(
                self.payload("bad.py", "broken"),
                kind=RefactoringKind.OTHER,
            )
        self.assertIsNone(coordinator.pending)
        self.assertIsNone(self.editor.pending)
        self.assertFalse((self.root / "bad.py").exists())

    def test_rename_requires_successful_safety_analysis(self) -> None:
        coordinator = RefactoringCoordinator(self.editor, Validator())
        with self.assertRaisesRegex(WorkspaceError, "engelledi"):
            coordinator.prepare(
                self.payload("sample.py", "value = 2\n"),
                kind=RefactoringKind.RENAME_SYMBOL,
                symbol="old_name",
                new_name="new_name",
                rename_safety=RenameSafety(False),
            )
        self.assertIsNone(self.editor.pending)

    def test_reject_leaves_workspace_unchanged(self) -> None:
        target = self.root / "sample.py"
        target.write_text("value = 1\n", encoding="utf-8")
        coordinator = RefactoringCoordinator(self.editor, Validator())
        plan = coordinator.prepare(
            self.payload("sample.py", "value = 2\n"),
            kind=RefactoringKind.OTHER,
        )
        coordinator.reject(plan.plan_id)
        self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
        self.assertIsNone(coordinator.pending)
        self.assertIsNone(self.editor.pending)


if __name__ == "__main__":
    unittest.main()
