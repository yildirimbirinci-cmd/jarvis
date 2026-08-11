from __future__ import annotations

import ast
from pathlib import Path


def method_source(method_name: str) -> str:
    text = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    segment = ast.get_source_segment(text, child)
                    assert segment is not None
                    return segment
    raise AssertionError(f"missing MainWindow.{method_name}")


def test_submit_cancel_fast_path_includes_pending_queue() -> None:
    source = method_source("submit")
    assert "pending_tasks = self.task_orchestrator.pending" in source
    assert "or bool(pending_tasks)" in source


def test_submit_text_cancel_fast_path_includes_pending_queue() -> None:
    source = method_source("submit_text")
    assert "pending_tasks = self.task_orchestrator.pending" in source
    assert "or bool(pending_tasks)" in source


def test_cancel_active_task_falls_back_to_pending_task() -> None:
    source = method_source("cancel_active_task")
    assert "pending = self.task_orchestrator.pending" in source
    assert "cancel_pending(record.task_id" in source
    assert "self._pending_worker_jobs.pop(record.task_id, None)" in source
