from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.refactoring_coordinator import RefactoringCoordinator
from artmach_assistant.core.rename_symbol_refactoring import RenameSymbolRefactoring, RenameSymbolRequest


class FakeWorkspace:
    def __init__(self, root: Path): self.root = root
    def require_root(self): return self.root
    def safe_path(self, path):
        target = (self.root / path).resolve(strict=False); target.relative_to(self.root); return target
    def read_text(self, path, max_chars): return self.safe_path(path).read_text(encoding="utf-8")[:max_chars]
    def invalidate_index(self): pass

class Validator:
    def validate(self, root, changes): return SimpleNamespace(is_valid=True, issues=())

class Analyzer:
    def __init__(self, result): self.result = result
    def analyze(self, old, new): return self.result


def record(path, line, column): return SimpleNamespace(path=path, line=line, column=column)


class RenameSymbolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        editor = EditManager(FakeWorkspace(self.root)); self.coordinator = RefactoringCoordinator(editor, Validator())
    def tearDown(self): self.temp.cleanup()

    def test_prepares_multi_file_exact_location_rename(self):
        (self.root / "a.py").write_text("def old(value):\n    return value\n", encoding="utf-8")
        (self.root / "b.py").write_text("from a import old\nresult = old(3)\n", encoding="utf-8")
        target = SimpleNamespace(definitions=(record("a.py",1,4),), references=(record("b.py",1,14), record("b.py",2,9)))
        safety = SimpleNamespace(safe=True, issues=(), target=target, impact=SimpleNamespace(files=()))
        plan = RenameSymbolRefactoring(self.coordinator, Analyzer(safety)).prepare(RenameSymbolRequest("old", "fresh"))
        self.assertEqual(set(plan.changed_paths), {"a.py", "b.py"})
        contents = {item.path:item.new_content for item in plan.proposal.files}
        self.assertIn("def fresh", contents["a.py"]); self.assertIn("import fresh", contents["b.py"]); self.assertIn("fresh(3)", contents["b.py"])
        self.assertEqual((self.root / "a.py").read_text(), "def old(value):\n    return value\n")

    def test_rejects_stale_index_location(self):
        (self.root / "a.py").write_text("def changed():\n    pass\n", encoding="utf-8")
        safety = SimpleNamespace(safe=True, issues=(), target=SimpleNamespace(definitions=(record("a.py",1,4),), references=()), impact=SimpleNamespace(files=()))
        with self.assertRaisesRegex(RuntimeError, "indekslendikten sonra değişmiş"):
            RenameSymbolRefactoring(self.coordinator, Analyzer(safety)).prepare(RenameSymbolRequest("old", "fresh"))

    def test_rejects_unsafe_analysis(self):
        safety = SimpleNamespace(safe=False, issues=(), target=SimpleNamespace(definitions=(), references=()), impact=SimpleNamespace(files=()))
        with self.assertRaisesRegex(RuntimeError, "güvenlik analizi"):
            RenameSymbolRefactoring(self.coordinator, Analyzer(safety)).prepare(RenameSymbolRequest("old", "fresh"))

if __name__ == "__main__": unittest.main()
