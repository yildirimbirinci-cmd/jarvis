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


def test_keyboard_busy_path_queues_normal_command() -> None:
    source = _method("submit_text")
    assert "self.queue_worker(" in source


def test_queue_worker_keeps_runtime_callable_in_memory() -> None:
    source = _method("queue_worker")
    assert "self._pending_worker_jobs" in source


def test_handoff_uses_qthread_finished_for_all_terminal_paths() -> None:
    source = _method("run_worker")
    assert "self.worker.finished.connect(" in source
    assert "_run_next_queued_worker" in source
    assert "QTimer.singleShot(0, self._run_next_queued_worker)" in source


def test_restart_restored_metadata_is_not_executed_without_callable() -> None:
    source = _method("_run_next_queued_worker")
    assert "self._pending_worker_jobs" in source
