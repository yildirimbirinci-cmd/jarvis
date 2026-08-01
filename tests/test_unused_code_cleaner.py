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

from artmach_assistant.core.dead_code_detector import (
    DeadCodeCandidate,
    DeadCodeReport,
)
from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator
from artmach_assistant.core.unused_code_cleaner import UnusedCodeCleaner


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
        return self.safe_path(relative_path).read_text(encoding="utf-8")

    def invalidate_index(self) -> None:
        self.invalidated = True


class ValidResult:
    is_valid = True
    issues = ()


class FakeValidator:
    def validate(self, root: object, changes: object) -> ValidResult:
        return ValidResult()


class FakeDetector:
    def __init__(self, root: Path, candidates: tuple[DeadCodeCandidate, ...]) -> None:
        self.root = root
        self.candidates = candidates

    def analyze(self, paths=None, *, limit: int = 200) -> DeadCodeReport:
        return DeadCodeReport(str(self.root), self.candidates[:limit], 1, 0)


class UnusedCodeCleanerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = FakeWorkspace(self.root)
        self.editor = EditManager(self.workspace)
        self.coordinator = RefactoringCoordinator(self.editor, FakeValidator())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, content: str, path: str = "sample.py") -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def cleaner(self, candidates: tuple[DeadCodeCandidate, ...]) -> UnusedCodeCleaner:
        return UnusedCodeCleaner(
            self.coordinator,
            FakeDetector(self.root, candidates),
        )

    def test_removes_private_definition_and_unused_import_only_after_apply(self) -> None:
        target = self.write(
            "import os\nimport sys\n\n"
            "def _unused():\n    return os.getcwd()\n\n"
            "def public():\n    return sys.version\n"
        )
        candidate = DeadCodeCandidate(
            path="sample.py", line=4, column=0, name="_unused",
            kind="function", reason="unused",
        )
        plan = self.cleaner((candidate,)).prepare(["sample.py"])
        self.assertIn("_unused", plan.preview())
        self.assertIn("def _unused", target.read_text(encoding="utf-8"))

        self.coordinator.apply(plan.plan_id)
        updated = target.read_text(encoding="utf-8")
        self.assertNotIn("def _unused", updated)
        self.assertNotIn("import os", updated)
        self.assertIn("import sys", updated)
        self.assertIn("def public", updated)

    def test_decorated_definition_is_removed_with_decorator(self) -> None:
        target = self.write(
            "def marker(fn):\n    return fn\n\n"
            "@marker\n"
            "def _unused():\n    return 1\n"
        )
        candidate = DeadCodeCandidate(
            path="sample.py", line=5, column=0, name="_unused",
            kind="function", reason="unused",
        )
        plan = self.cleaner((candidate,)).prepare(["sample.py"], include_imports=False)
        self.coordinator.apply(plan.plan_id)
        updated = target.read_text(encoding="utf-8")
        self.assertNotIn("@marker", updated)
        self.assertNotIn("def _unused", updated)
        self.assertIn("def marker", updated)

    def test_stale_candidate_is_rejected_without_pending_proposal(self) -> None:
        self.write("def _unused():\n    return 1\n")
        candidate = DeadCodeCandidate(
            path="sample.py", line=2, column=0, name="_unused",
            kind="function", reason="unused",
        )
        with self.assertRaisesRegex(WorkspaceError, "eşleşmiyor"):
            self.cleaner((candidate,)).prepare(["sample.py"])
        self.assertIsNone(self.coordinator.pending)
        self.assertIsNone(self.editor.pending)

    def test_more_than_eight_changed_files_is_rejected(self) -> None:
        candidates = []
        for index in range(9):
            path = f"f{index}.py"
            self.write("def _unused():\n    return 1\n", path)
            candidates.append(DeadCodeCandidate(
                path=path, line=1, column=0, name="_unused",
                kind="function", reason="unused",
            ))
        with self.assertRaisesRegex(WorkspaceError, "8 dosyadan"):
            self.cleaner(tuple(candidates)).prepare()
        self.assertIsNone(self.coordinator.pending)

    def test_no_candidate_is_rejected(self) -> None:
        self.write("def public():\n    return 1\n")
        with self.assertRaisesRegex(WorkspaceError, "bulunamadı"):
            self.cleaner(()).prepare(["sample.py"])


if __name__ == "__main__":
    unittest.main()
