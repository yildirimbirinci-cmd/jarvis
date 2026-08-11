from __future__ import annotations

import ast
from pathlib import Path


def method_source(name: str) -> str:
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


def test_finished_handoff_waits_until_qthread_has_fully_stopped() -> None:
    source = method_source("run_worker")
    assert "self.worker.finished.connect(" in source
    assert "lambda: QTimer.singleShot(0, self._run_next_queued_worker)" in source
    assert "self.worker.finished.connect(self._run_next_queued_worker)" not in source


def test_next_queued_worker_still_enforces_fifo_head() -> None:
    source = method_source("_run_next_queued_worker")
    assert "next_record = pending[0]" in source
    assert "_queued_task_id=next_record.task_id" in source
    assert "self._pending_worker_jobs.pop(next_record.task_id, None)" in source


def test_busy_keyboard_commands_are_still_queued_with_original_callable() -> None:
    source = method_source("submit_text")
    assert "self.queue_worker(" in source
    assert "lambda: self.engine.handle(text)" in source
