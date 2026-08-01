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
from artmach_assistant.core.move_function_refactoring import (
    MoveFunctionRefactoring,
    MoveFunctionRequest,
)
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


class MoveFunctionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        editor = EditManager(FakeWorkspace(self.root))
        self.service = MoveFunctionRefactoring(RefactoringCoordinator(editor, Validator()))
    def tearDown(self): self.temp.cleanup()

    def test_moves_function_copies_import_and_updates_importers(self):
        (self.root / "source.py").write_text(
            "import os\n\ndef home(name: str = 'x'):\n    return os.path.join('root', name)\n\ndef keep():\n    return home('kept')\n",
            encoding="utf-8",
        )
        (self.root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "consumer.py").write_text(
            "from source import home, keep\nvalue = home()\n", encoding="utf-8"
        )
        plan = self.service.prepare(MoveFunctionRequest("source.py", "home", "target.py"))
        contents = {item.path: item.new_content for item in plan.proposal.files}
        self.assertEqual(set(contents), {"source.py", "target.py", "consumer.py"})
        self.assertNotIn("def home", contents["source.py"])
        self.assertIn("from target import home", contents["source.py"])
        self.assertIn("import os", contents["target.py"])
        self.assertIn("def home", contents["target.py"])
        self.assertIn("from source import keep", contents["consumer.py"])
        self.assertIn("from target import home", contents["consumer.py"])
        self.assertIn("def home", (self.root / "source.py").read_text(encoding="utf-8"))
        for path, content in contents.items(): compile(content, path, "exec")

    def test_moves_async_function_to_missing_destination(self):
        (self.root / "source.py").write_text(
            "import asyncio\n\nasync def pause():\n    await asyncio.sleep(0)\n",
            encoding="utf-8",
        )
        plan = self.service.prepare(MoveFunctionRequest("source.py", "pause", "pkg/target.py"))
        contents = {item.path: item.new_content for item in plan.proposal.files}
        self.assertIn("import asyncio", contents["pkg/target.py"])
        self.assertIn("async def pause", contents["pkg/target.py"])
        compile(contents["pkg/target.py"], "pkg/target.py", "exec")

    def test_rejects_source_local_dependency(self):
        (self.root / "source.py").write_text(
            "VALUE = 2\n\ndef calculate(number):\n    return number + VALUE\n",
            encoding="utf-8",
        )
        (self.root / "target.py").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "yerel tanımlara bağlı: VALUE"):
            self.service.prepare(MoveFunctionRequest("source.py", "calculate", "target.py"))

    def test_rejects_duplicate_destination_function(self):
        (self.root / "source.py").write_text("def work():\n    return 1\n", encoding="utf-8")
        (self.root / "target.py").write_text("def work():\n    return 2\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "zaten work"):
            self.service.prepare(MoveFunctionRequest("source.py", "work", "target.py"))

    def test_rejects_relative_import_source(self):
        (self.root / "source.py").write_text(
            "from .base import VALUE\n\ndef work():\n    return VALUE\n", encoding="utf-8"
        )
        (self.root / "target.py").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "göreli import"):
            self.service.prepare(MoveFunctionRequest("source.py", "work", "target.py"))


if __name__ == "__main__": unittest.main()
