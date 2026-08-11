from __future__ import annotations

import ast
from pathlib import Path


def _method(name: str) -> str:
    text = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    segment = ast.get_source_segment(text, child)
                    assert segment
                    return segment
    raise AssertionError(f"missing MainWindow.{name}")


def test_cancel_fallback_uses_atomic_latest_pending_operation() -> None:
    source = _method("cancel_active_task")
    assert 'cancel_latest_pending("kullanıcı iptali")' in source
    assert "self._pending_worker_jobs.pop(removed.task_id, None)" in source
    assert "pending[-1]" not in source


def test_worker_terminal_paths_still_handoff_to_fifo() -> None:
    source = _method("run_worker")
    assert "self.worker.finished.connect(self._run_next_queued_worker)" in source
    assert "self.task_orchestrator.finish(record.task_id)" in source
    assert "cancelled=cancelled" in source
