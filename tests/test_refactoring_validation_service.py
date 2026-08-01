from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator, RefactoringKind
from artmach_assistant.core.refactoring_validation_service import RefactoringValidationService


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.invalidations = 0

    def require_root(self) -> Path:
        return self.root

    def safe_path(self, path: str) -> Path:
        target = (self.root / path).resolve()
        target.relative_to(self.root)
        return target

    def read_text(self, path: str, max_chars: int) -> str:
        return self.safe_path(path).read_text(encoding="utf-8")[:max_chars]

    def invalidate_index(self) -> None:
        self.invalidations += 1


class AlwaysValid:
    def validate(self, root: object, changes: object) -> object:
        return type("Result", (), {"issues": (), "is_valid": True})()


class RefactoringValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.editor = EditManager(self.workspace)
        self.service = RefactoringValidationService()
        self.coordinator = RefactoringCoordinator(
            self.editor,
            AlwaysValid(),
            final_validator=self.service,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def prepare(self, content: str = "value = 2\n"):
        (self.root / "a.py").write_text("value = 1\n", encoding="utf-8")
        raw = json.dumps({
            "summary": "change",
            "files": [{"path": "a.py", "reason": "test", "content": content}],
        })
        return self.coordinator.prepare(raw, kind=RefactoringKind.OTHER)

    def test_valid_pending_plan_returns_snapshot_token(self) -> None:
        plan = self.prepare()
        report = self.service.validate(self.editor, plan)
        self.assertTrue(report.is_valid)
        self.assertEqual(report.checked_files, ("a.py",))
        self.assertEqual(len(report.validation_token), 64)

    def test_stale_file_is_rejected_at_apply_time(self) -> None:
        plan = self.prepare()
        (self.root / "a.py").write_text("external = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "stale_content"):
            self.coordinator.apply(plan.plan_id)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "external = 1\n")

    def test_mutated_proposal_is_revalidated(self) -> None:
        plan = self.prepare()
        plan.proposal.files[0].new_content = "def broken(:\n"
        with self.assertRaisesRegex(WorkspaceError, "python_syntax"):
            self.coordinator.apply(plan.plan_id)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_pending_identity_mismatch_is_rejected(self) -> None:
        plan = self.prepare()
        self.editor.pending = None
        report = self.service.validate(self.editor, plan)
        self.assertFalse(report.is_valid)
        self.assertIn("pending_mismatch", {issue.code for issue in report.errors})

    def test_plan_warnings_do_not_block_apply(self) -> None:
        plan = self.prepare()
        object.__setattr__(plan, "warnings", ("Review this change",))
        report = self.service.validate(self.editor, plan)
        self.assertTrue(report.is_valid)
        self.assertEqual(len(report.warnings), 1)
        self.coordinator.apply(plan.plan_id)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "value = 2\n")


if __name__ == "__main__":
    unittest.main()
