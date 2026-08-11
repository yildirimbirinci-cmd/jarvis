from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _method(name: str) -> str:
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    text = ast.get_source_segment(source, child)
                    assert text
                    return text
    raise AssertionError(f"MainWindow.{name} not found")


def test_final_worker_handoff_is_deferred_until_qthread_event_returns() -> None:
    source = _method("run_worker")
    assert "self.worker.finished.connect(" in source
    assert "QTimer.singleShot(0, self._run_next_queued_worker)" in source
    assert "self.worker.finished.connect(self._run_next_queued_worker)" not in source


def test_final_fifo_handoff_uses_pending_head_and_original_job() -> None:
    source = _method("_run_next_queued_worker")
    assert "pending[0]" in source
    assert "_pending_worker_jobs" in source
    assert "_queued_task_id" in source


def test_final_cancel_path_cancels_active_before_pending_fallback() -> None:
    source = _method("cancel_active_task")
    active_pos = source.find("cancel_active")
    pending_pos = source.find("cancel_latest_pending")
    assert active_pos >= 0
    assert pending_pos > active_pos


def test_final_submit_paths_keep_live_cancel_fast_path() -> None:
    submit = _method("submit")
    submit_text = _method("submit_text")
    assert "live_cancel" in submit
    assert "task_in_flight" in submit
    assert "live_cancel" in submit_text
    assert "task_in_flight" in submit_text


def test_final_queue_path_retains_callable_until_fifo_execution() -> None:
    source = _method("queue_worker")
    assert "_pending_worker_jobs" in source
    assert "enqueue" in source
