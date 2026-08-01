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

from artmach_assistant.core.complexity_analyzer import (
    ComplexityAnalyzer,
    ComplexityThresholds,
)


class FakeWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)

    def require_root(self) -> Path:
        return self.root

    def safe_path(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve(strict=False)
        target.relative_to(self.root)
        return target

    def read_text(self, relative_path: str, max_chars: int) -> str:
        text = self.safe_path(relative_path).read_text(encoding="utf-8")
        if len(text) > max_chars:
            raise WorkspaceError("too large")
        return text


class ComplexityAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = FakeWorkspace(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def test_reports_nested_decisions_as_high_risk(self) -> None:
        source = (
            "def process(value):\n"
            "    if value:\n"
            "        for item in value:\n"
            "            if item > 0:\n"
            "                while item > 1:\n"
            "                    item -= 1\n"
            "    return value\n"
        )
        analyzer = ComplexityAnalyzer(
            self.workspace,
            ComplexityThresholds(
                cyclomatic_warning=4,
                cognitive_warning=6,
                nesting_warning=4,
                function_lines_warning=50,
            ),
        )
        items = analyzer.analyze_content("sample.py", source)
        function = next(item for item in items if item.kind == "function")
        self.assertEqual(function.cyclomatic, 5)
        self.assertGreaterEqual(function.cognitive, 10)
        self.assertEqual(function.max_nesting, 4)
        self.assertEqual(function.risk, "high")

    def test_nested_function_is_not_counted_in_parent(self) -> None:
        source = (
            "def outer(flag):\n"
            "    def inner(values):\n"
            "        if values:\n"
            "            return 1\n"
            "        return 0\n"
            "    return inner(flag)\n"
        )
        items = ComplexityAnalyzer(self.workspace).analyze_content("sample.py", source)
        outer = next(item for item in items if item.qualified_name == "outer")
        self.assertEqual(outer.cyclomatic, 1)

    def test_class_and_method_metrics_are_reported(self) -> None:
        source = (
            "class Service:\n"
            "    def run(self, value, limit=3):\n"
            "        if value and limit:\n"
            "            return value\n"
            "        return None\n"
        )
        items = ComplexityAnalyzer(self.workspace).analyze_content("service.py", source)
        method = next(item for item in items if item.kind == "method")
        klass = next(item for item in items if item.kind == "class")
        self.assertEqual(method.qualified_name, "Service.run")
        self.assertEqual(method.parameter_count, 2)
        self.assertEqual(method.cyclomatic, 3)
        self.assertGreaterEqual(klass.cyclomatic, method.cyclomatic)

    def test_workspace_scan_skips_parse_failure_and_filters_low_risk(self) -> None:
        self.write("good.py", "def simple():\n    return 1\n")
        self.write("bad.py", "def broken(:\n")
        analyzer = ComplexityAnalyzer(self.workspace)
        report = analyzer.analyze(include_low_risk=False)
        self.assertEqual(report.files_scanned, 1)
        self.assertEqual(report.items, ())
        self.assertEqual(len(report.parse_failures), 1)
        self.assertIn("bad.py", report.parse_failures[0])

    def test_directory_scope_and_limit_are_deterministic(self) -> None:
        self.write("pkg/b.py", "def b(x):\n    if x:\n        return 1\n    return 0\n")
        self.write("pkg/a.py", "def a(x):\n    if x:\n        return 1\n    return 0\n")
        analyzer = ComplexityAnalyzer(
            self.workspace,
            ComplexityThresholds(cyclomatic_warning=2),
        )
        report = analyzer.analyze(["pkg"], limit=1)
        self.assertEqual(len(report.items), 1)
        self.assertEqual(report.items[0].path, "pkg/a.py")

    def test_invalid_limit_is_rejected(self) -> None:
        with self.assertRaisesRegex(WorkspaceError, "en az 1"):
            ComplexityAnalyzer(self.workspace).analyze(limit=0)


if __name__ == "__main__":
    unittest.main()
