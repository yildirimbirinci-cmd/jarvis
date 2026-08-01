from __future__ import annotations

import sys
import tempfile
import types
import unittest
from dataclasses import dataclass
from pathlib import Path


_workspace_module_name = "artmach_assistant.core.workspace"
_previous_workspace_module = sys.modules.get(_workspace_module_name)
workspace_stub = types.ModuleType(_workspace_module_name)


class WorkspaceError(RuntimeError):
    pass


class WorkspaceService:
    pass


workspace_stub.WorkspaceError = WorkspaceError
workspace_stub.WorkspaceService = WorkspaceService
sys.modules[_workspace_module_name] = workspace_stub
try:
    from artmach_assistant.core.edit_manager import EditManager
    from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator
    from artmach_assistant.core.safe_import_optimizer import SafeImportOptimizer
finally:
    if _previous_workspace_module is None:
        sys.modules.pop(_workspace_module_name, None)
    else:
        sys.modules[_workspace_module_name] = _previous_workspace_module


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


class Validator:
    def validate(self, root: object, changes: object) -> ValidationResult:
        return ValidationResult()


class SafeImportOptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.workspace = FakeWorkspace(self.root)
        editor = EditManager(self.workspace)  # type: ignore[arg-type]
        self.coordinator = RefactoringCoordinator(editor, Validator())
        self.optimizer = SafeImportOptimizer(self.coordinator)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, content: str) -> Path:
        path = self.root / "sample.py"
        path.write_text(content, encoding="utf-8")
        return path

    def test_removes_unused_alias_and_exact_duplicate(self) -> None:
        self.write(
            "import os, sys\n"
            "from pathlib import Path, PurePath\n"
            "from pathlib import Path, PurePath\n"
            "print(sys.version)\n"
            "print(Path('x'))\n"
        )
        result = self.optimizer.analyze("sample.py")
        self.assertEqual(set(result.removed_bindings), {"os", "PurePath"})
        self.assertEqual(result.removed_duplicates, 1)
        self.assertIn("import sys", result.content)
        self.assertIn("from pathlib import Path", result.content)
        self.assertNotIn("PurePath", result.content)

    def test_preserves_future_relative_star_noqa_and_multiline_imports(self) -> None:
        source = (
            "from __future__ import annotations\n"
            "from .local import value\n"
            "from package import *\n"
            "import optional_dependency  # noqa: F401\n"
            "from package import (\n"
            "    first,\n"
            "    second,\n"
            ")\n"
        )
        self.write(source)
        result = self.optimizer.analyze("sample.py")
        self.assertFalse(result.changed)
        self.assertEqual(result.content, source)
        self.assertEqual(result.preserved_risky_imports, 5)

    def test_all_export_keeps_imported_binding(self) -> None:
        source = "from package import PublicName\n__all__ = ['PublicName']\n"
        self.write(source)
        result = self.optimizer.analyze("sample.py")
        self.assertFalse(result.changed)
        self.assertIn("PublicName", result.content)


if __name__ == "__main__":
    unittest.main()
