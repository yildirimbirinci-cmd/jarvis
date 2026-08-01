from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

workspace_stub = types.ModuleType("artmach_assistant.core.workspace")
class WorkspaceError(RuntimeError):
    pass
class WorkspaceService:
    pass
workspace_stub.WorkspaceError = WorkspaceError
workspace_stub.WorkspaceService = WorkspaceService
sys.modules["artmach_assistant.core.workspace"] = workspace_stub

from artmach_assistant.core.edit_manager import EditManager, EditProposal, ProposedFileChange
from artmach_assistant.core.multi_file_refactoring import MultiFileRefactoring, RefactoringPatch
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
    def require_root(self) -> Path:
        return self.root
    def safe_path(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve(strict=False)
        target.relative_to(self.root)
        return target
    def read_text(self, relative_path: str, max_chars: int) -> str:
        return self.safe_path(relative_path).read_text(encoding="utf-8")[:max_chars]
    def invalidate_index(self) -> None:
        pass


class ValidResult:
    issues = ()
    is_valid = True
class Validator:
    def validate(self, root, changes):
        return ValidResult()


class MultiFileRefactoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.editor = EditManager(FakeWorkspace(self.root))
        self.coordinator = RefactoringCoordinator(self.editor, Validator())
        self.service = MultiFileRefactoring(self.coordinator)
    def tearDown(self):
        self.temp.cleanup()
    @staticmethod
    def proposal(path: str, old: str, new: str) -> EditProposal:
        return EditProposal("x", [ProposedFileChange(path, "test", old, new, True)])
    def test_merges_distinct_files_without_writing(self):
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "b.py").write_text("y = 1\n", encoding="utf-8")
        plan = self.service.prepare([
            RefactoringPatch("rename_symbol", self.proposal("a.py", "x = 1\n", "z = 1\n")),
            RefactoringPatch("optimize_imports", self.proposal("b.py", "y = 1\n", "y = 2\n")),
        ])
        self.assertEqual(set(plan.changed_paths), {"a.py", "b.py"})
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "x = 1\n")
    def test_rejects_conflicting_outputs(self):
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "çakışan"):
            self.service.prepare([
                RefactoringPatch("rename_symbol", self.proposal("a.py", "x = 1\n", "z = 1\n")),
                RefactoringPatch("optimize_imports", self.proposal("a.py", "x = 1\n", "x = 2\n")),
            ])
    def test_rejects_more_than_eight_files(self):
        patches = []
        for index in range(9):
            path = f"f{index}.py"
            old = f"x = {index}\n"
            (self.root / path).write_text(old, encoding="utf-8")
            patches.append(RefactoringPatch("other", self.proposal(path, old, f"x = {index + 1}\n")))
        with self.assertRaisesRegex(WorkspaceError, "en fazla 8"):
            self.service.prepare(patches)
    def test_apply_is_atomic_through_coordinator(self):
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "b.py").write_text("y = 1\n", encoding="utf-8")
        plan = self.service.prepare([
            RefactoringPatch("rename_symbol", self.proposal("a.py", "x = 1\n", "z = 1\n")),
            RefactoringPatch("optimize_imports", self.proposal("b.py", "y = 1\n", "y = 2\n")),
        ])
        self.coordinator.apply(plan.plan_id)
        self.assertEqual((self.root / "a.py").read_text(encoding="utf-8"), "z = 1\n")
        self.assertEqual((self.root / "b.py").read_text(encoding="utf-8"), "y = 2\n")

if __name__ == "__main__":
    unittest.main()
