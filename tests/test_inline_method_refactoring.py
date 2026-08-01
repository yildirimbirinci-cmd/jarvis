from __future__ import annotations

import tempfile
import unittest
import sys
import types
from dataclasses import dataclass
from pathlib import Path

workspace_stub = types.ModuleType("artmach_assistant.core.workspace")
class WorkspaceError(RuntimeError):
    pass
class WorkspaceService:
    pass
workspace_stub.WorkspaceError = WorkspaceError
workspace_stub.WorkspaceService = WorkspaceService
sys.modules["artmach_assistant.core.workspace"] = workspace_stub

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.inline_method_refactoring import (
    InlineMethodRefactoring,
    InlineMethodRequest,
)
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
    def require_root(self) -> Path:
        return self.root
    def safe_path(self, path: str) -> Path:
        target = (self.root / path).resolve()
        target.relative_to(self.root.resolve())
        return target
    def read_text(self, path: str, max_chars: int) -> str:
        return self.safe_path(path).read_text(encoding="utf-8")[:max_chars]
    def invalidate_index(self) -> None:
        pass

@dataclass(frozen=True)
class Valid:
    issues: tuple[object, ...] = ()
    is_valid: bool = True
class Validator:
    def validate(self, root: object, changes: object) -> Valid:
        return Valid()


class InlineMethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        editor = EditManager(FakeWorkspace(self.root))  # type: ignore[arg-type]
        self.service = InlineMethodRefactoring(RefactoringCoordinator(editor, Validator()))
    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_inlines_module_function_and_removes_definition(self) -> None:
        source = "def double(value):\n    return value * 2\n\nresult = double(4)\n"
        (self.root / "sample.py").write_text(source, encoding="utf-8")
        plan = self.service.prepare(InlineMethodRequest("sample.py", "double"))
        content = plan.proposal.files[0].new_content
        self.assertNotIn("def double", content)
        self.assertIn("result = 4 * 2", content)
        compile(content, "sample.py", "exec")

    def test_inlines_instance_method_with_receiver_and_keyword(self) -> None:
        source = (
            "class Worker:\n"
            "    def scale(self, value, factor=2):\n"
            "        return self.base + value * factor\n\n"
            "    def run(self):\n"
            "        return self.scale(value=3, factor=4)\n"
        )
        (self.root / "sample.py").write_text(source, encoding="utf-8")
        plan = self.service.prepare(InlineMethodRequest("sample.py", "scale"))
        content = plan.proposal.files[0].new_content
        self.assertNotIn("def scale", content)
        self.assertIn("return self.base + 3 * 4", content)
        compile(content, "sample.py", "exec")

    def test_rejects_multi_statement_function(self) -> None:
        source = "def calculate(value):\n    total = value + 1\n    return total\n\nresult = calculate(2)\n"
        (self.root / "sample.py").write_text(source, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "tek bir return"):
            self.service.prepare(InlineMethodRequest("sample.py", "calculate"))

    def test_rejects_definition_removal_when_reference_is_passed_around(self) -> None:
        source = "def double(value):\n    return value * 2\n\ncallback = double\nresult = double(4)\n"
        (self.root / "sample.py").write_text(source, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "tanım kaldırılamıyor"):
            self.service.prepare(InlineMethodRequest("sample.py", "double"))


if __name__ == "__main__":
    unittest.main()
