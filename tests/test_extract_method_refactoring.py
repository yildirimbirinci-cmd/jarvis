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
from artmach_assistant.core.extract_method_refactoring import (
    ExtractMethodRefactoring,
    ExtractMethodRequest,
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


class ExtractMethodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        workspace = FakeWorkspace(self.root)
        self.editor = EditManager(workspace)  # type: ignore[arg-type]
        self.service = ExtractMethodRefactoring(
            RefactoringCoordinator(self.editor, Validator())
        )
    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_extracts_module_function_with_input_and_output(self) -> None:
        source = "def calculate(value):\n    doubled = value * 2\n    total = doubled + 1\n    return total\n"
        (self.root / "sample.py").write_text(source, encoding="utf-8")
        plan = self.service.prepare(ExtractMethodRequest("sample.py", 2, 2, "double_value"))
        content = plan.proposal.files[0].new_content
        self.assertIn("def double_value(value):", content)
        self.assertIn("return doubled", content)
        self.assertIn("doubled = double_value(value)", content)
        compile(content, "sample.py", "exec")

    def test_extracts_class_method_using_receiver(self) -> None:
        source = "class Worker:\n    def run(self, value):\n        result = self.scale * value\n        print(result)\n"
        (self.root / "sample.py").write_text(source, encoding="utf-8")
        plan = self.service.prepare(ExtractMethodRequest("sample.py", 3, 3, "scale_value"))
        content = plan.proposal.files[0].new_content
        self.assertIn("def scale_value(self, value):", content)
        self.assertIn("result = self.scale_value(value)", content)
        compile(content, "sample.py", "exec")

    def test_rejects_partial_statement_selection(self) -> None:
        source = "def run(flag):\n    if flag:\n        print('x')\n"
        (self.root / "sample.py").write_text(source, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "tam ve ardışık"):
            self.service.prepare(ExtractMethodRequest("sample.py", 2, 2, "show"))

    def test_rejects_return_in_selection(self) -> None:
        source = "def run(value):\n    return value + 1\n"
        (self.root / "sample.py").write_text(source, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Return"):
            self.service.prepare(ExtractMethodRequest("sample.py", 2, 2, "finish"))

    def test_preserves_outer_loop_break_and_continue_as_helper_result(self) -> None:
        source = (
            "class Worker:\n"
            "    def run(self):\n"
            "        while True:\n"
            "            if self.ready:\n"
            "                try:\n"
            "                    self.work()\n"
            "                except InterruptedError:\n"
            "                    break\n"
            "                except Exception:\n"
            "                    self.msleep(250)\n"
            "                    continue\n"
            "                self.done.emit()\n"
            "                continue\n"
            "            self.idle()\n"
        )
        (self.root / "sample.py").write_text(source, encoding="utf-8")

        plan = self.service.prepare(
            ExtractMethodRequest(
                "sample.py",
                4,
                13,
                "_handle_ready",
                preserve_loop_control=True,
            )
        )

        content = plan.proposal.files[0].new_content
        self.assertIn('return "break"', content)
        self.assertEqual(content.count('return "continue"'), 2)
        self.assertIn("return None", content)
        self.assertIn("self.msleep(250)", content)
        self.assertIn('extract_action = self._handle_ready()', content)
        self.assertIn('if extract_action == "break":\n                break', content)
        self.assertIn(
            'if extract_action == "continue":\n                continue', content
        )
        self.assertIn("            self.idle()", content)
        compile(content, "sample.py", "exec")

    def test_loop_control_remains_opt_in(self) -> None:
        source = (
            "def run():\n"
            "    while True:\n"
            "        if ready():\n"
            "            break\n"
        )
        (self.root / "sample.py").write_text(source, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Break"):
            self.service.prepare(
                ExtractMethodRequest("sample.py", 3, 4, "_handle_ready")
            )


if __name__ == "__main__":
    unittest.main()
