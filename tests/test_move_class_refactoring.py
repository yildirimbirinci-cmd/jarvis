from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
from artmach_assistant.core.move_class_refactoring import MoveClassRefactoring, MoveClassRequest
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator


class FakeWorkspace:
    def __init__(self, root: Path): self.root = root
    def require_root(self): return self.root
    def safe_path(self, path):
        target = (self.root / path).resolve(strict=False); target.relative_to(self.root); return target
    def read_text(self, path, max_chars): return self.safe_path(path).read_text(encoding="utf-8")[:max_chars]
    def invalidate_index(self): pass


class Validator:
    def validate(self, root, changes): return SimpleNamespace(is_valid=True, issues=())


class MoveClassTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        editor = EditManager(FakeWorkspace(self.root))
        self.service = MoveClassRefactoring(RefactoringCoordinator(editor, Validator()))
    def tearDown(self): self.temp.cleanup()

    def test_moves_class_copies_required_import_and_updates_importers(self):
        (self.root / "source.py").write_text(
            "from dataclasses import dataclass\nimport os\n\n@dataclass\nclass Worker:\n    name: str\n\n    def home(self):\n        return os.path.expanduser('~')\n\ndef keep():\n    return 1\n",
            encoding="utf-8",
        )
        (self.root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "consumer.py").write_text("from source import Worker, keep\nitem = Worker('x')\n", encoding="utf-8")
        plan = self.service.prepare(MoveClassRequest("source.py", "Worker", "target.py"))
        contents = {item.path: item.new_content for item in plan.proposal.files}
        self.assertEqual(set(contents), {"source.py", "target.py", "consumer.py"})
        self.assertNotIn("class Worker", contents["source.py"])
        self.assertIn("from dataclasses import dataclass", contents["target.py"])
        self.assertIn("import os", contents["target.py"])
        self.assertIn("class Worker", contents["target.py"])
        self.assertIn("from source import keep", contents["consumer.py"])
        self.assertIn("from target import Worker", contents["consumer.py"])
        self.assertIn("class Worker", (self.root / "source.py").read_text(encoding="utf-8"))
        for path, content in contents.items(): compile(content, path, "exec")

    def test_creates_missing_destination(self):
        (self.root / "source.py").write_text("class Worker:\n    pass\n", encoding="utf-8")
        plan = self.service.prepare(MoveClassRequest("source.py", "Worker", "pkg/target.py"))
        contents = {item.path: item.new_content for item in plan.proposal.files}
        self.assertEqual(contents["pkg/target.py"], "class Worker:\n    pass\n")

    def test_rejects_duplicate_destination_class(self):
        (self.root / "source.py").write_text("class Worker:\n    pass\n", encoding="utf-8")
        (self.root / "target.py").write_text("class Worker:\n    pass\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "zaten Worker"):
            self.service.prepare(MoveClassRequest("source.py", "Worker", "target.py"))

    def test_rejects_relative_import_source(self):
        (self.root / "source.py").write_text("from .base import Base\nclass Worker(Base):\n    pass\n", encoding="utf-8")
        (self.root / "target.py").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "göreli import"):
            self.service.prepare(MoveClassRequest("source.py", "Worker", "target.py"))


if __name__ == "__main__": unittest.main()
