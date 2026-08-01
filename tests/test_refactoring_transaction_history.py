from __future__ import annotations

import json
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

from artmach_assistant.core.edit_manager import EditManager
from artmach_assistant.core.refactoring_transaction_history import RefactoringTransactionHistory


class FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root
        self.invalidations = 0
    def require_root(self):
        return self.root
    def safe_path(self, path: str):
        target = (self.root / path).resolve(strict=False)
        target.relative_to(self.root)
        return target
    def read_text(self, path: str, max_chars: int):
        return self.safe_path(path).read_text(encoding="utf-8")[:max_chars]
    def invalidate_index(self):
        self.invalidations += 1


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.ws = FakeWorkspace(self.root)
        self.editor = EditManager(self.ws)
        self.history = RefactoringTransactionHistory(self.ws)
    def tearDown(self):
        self.temp.cleanup()
    def apply(self, files):
        self.editor.create_proposal(json.dumps({"summary": "x", "files": files}))
        self.editor.apply()
    def test_undo_and_redo_existing_file(self):
        target = self.root / "a.py"
        target.write_text("x = 1\n", encoding="utf-8")
        self.apply([{"path": "a.py", "content": "x = 2\n", "reason": "test"}])
        self.history.undo()
        self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")
        self.history.redo()
        self.assertEqual(target.read_text(encoding="utf-8"), "x = 2\n")
    def test_undo_removes_new_file_and_redo_restores(self):
        target = self.root / "new.py"
        self.apply([{"path": "new.py", "content": "x = 1\n", "reason": "test"}])
        self.history.undo()
        self.assertFalse(target.exists())
        self.history.redo()
        self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")
    def test_undo_refuses_stale_file(self):
        target = self.root / "a.py"
        target.write_text("x = 1\n", encoding="utf-8")
        self.apply([{"path": "a.py", "content": "x = 2\n", "reason": "test"}])
        target.write_text("external = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "değişmiş"):
            self.history.undo()
        self.assertEqual(target.read_text(encoding="utf-8"), "external = 1\n")
    def test_latest_transaction_is_undone_first(self):
        target = self.root / "a.py"
        target.write_text("x = 1\n", encoding="utf-8")
        self.apply([{"path": "a.py", "content": "x = 2\n", "reason": "one"}])
        self.apply([{"path": "a.py", "content": "x = 3\n", "reason": "two"}])
        self.history.undo()
        self.assertEqual(target.read_text(encoding="utf-8"), "x = 2\n")
    def test_report_lists_applied_checkpoint(self):
        target = self.root / "a.py"
        target.write_text("x = 1\n", encoding="utf-8")
        self.apply([{"path": "a.py", "content": "x = 2\n", "reason": "one"}])
        report = self.history.report()
        self.assertIn("SON KOD SÜRÜMLERİ", report)
        self.assertIn("a.py", report)

    def test_undo_refuses_tampered_checkpoint_content(self):
        target = self.root / "a.py"
        target.write_text("x = 1\n", encoding="utf-8")
        self.apply([{"path": "a.py", "content": "x = 2\n", "reason": "test"}])
        checkpoint = max(
            (self.root / ".artmach_assistant" / "checkpoints").iterdir()
        )
        (checkpoint / "before" / "a.py").write_text(
            "tampered = True\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(WorkspaceError, "değiştirilmiş veya bozulmuş"):
            self.history.undo()

        self.assertEqual(target.read_text(encoding="utf-8"), "x = 2\n")

    def test_redo_refuses_tampered_checkpoint_content(self):
        target = self.root / "a.py"
        target.write_text("x = 1\n", encoding="utf-8")
        self.apply([{"path": "a.py", "content": "x = 2\n", "reason": "test"}])
        checkpoint = max(
            (self.root / ".artmach_assistant" / "checkpoints").iterdir()
        )
        self.history.undo()
        (checkpoint / "after" / "a.py").write_text(
            "tampered = True\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(WorkspaceError, "değiştirilmiş veya bozulmuş"):
            self.history.redo()

        self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_recovery_completes_fully_applied_prepared_checkpoint(self):
        target = self.root / "a.py"
        target.write_text("x = 1\n", encoding="utf-8")
        self.apply([{"path": "a.py", "content": "x = 2\n", "reason": "test"}])
        checkpoint = max(
            (self.root / ".artmach_assistant" / "checkpoints").iterdir()
        )
        (checkpoint / "state.json").write_text(
            '{"state":"prepared"}', encoding="utf-8"
        )

        result = self.history.recover_incomplete()

        self.assertIn("uygulama tamamlandı", result)
        self.assertEqual(self.history._read_state(checkpoint), "applied")
        self.assertEqual(target.read_text(encoding="utf-8"), "x = 2\n")

    def test_recovery_rolls_back_partially_applied_checkpoint(self):
        first = self.root / "a.py"
        second = self.root / "b.py"
        first.write_text("a = 1\n", encoding="utf-8")
        second.write_text("b = 1\n", encoding="utf-8")
        self.apply([
            {"path": "a.py", "content": "a = 2\n", "reason": "test"},
            {"path": "b.py", "content": "b = 2\n", "reason": "test"},
        ])
        checkpoint = max(
            (self.root / ".artmach_assistant" / "checkpoints").iterdir()
        )
        (checkpoint / "state.json").write_text(
            '{"state":"prepared"}', encoding="utf-8"
        )
        second.write_text("b = 1\n", encoding="utf-8")

        result = self.history.recover_incomplete()

        self.assertIn("geri alındı", result)
        self.assertEqual(first.read_text(encoding="utf-8"), "a = 1\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "b = 1\n")
        self.assertEqual(self.history._read_state(checkpoint), "rolled_back")

    def test_recovery_refuses_unknown_external_change(self):
        target = self.root / "a.py"
        target.write_text("x = 1\n", encoding="utf-8")
        self.apply([{"path": "a.py", "content": "x = 2\n", "reason": "test"}])
        checkpoint = max(
            (self.root / ".artmach_assistant" / "checkpoints").iterdir()
        )
        (checkpoint / "state.json").write_text(
            '{"state":"prepared"}', encoding="utf-8"
        )
        target.write_text("external = True\n", encoding="utf-8")

        with self.assertRaisesRegex(WorkspaceError, "dışarıdan değiştirilmiş"):
            self.history.recover_incomplete()

        self.assertEqual(target.read_text(encoding="utf-8"), "external = True\n")

if __name__ == "__main__":
    unittest.main()
