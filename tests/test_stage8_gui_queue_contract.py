from __future__ import annotations

import ast
from pathlib import Path


def method_source(path: Path, class_name: str, method_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    segment = ast.get_source_segment(text, child)
                    assert segment is not None
                    return segment
    raise AssertionError(f"missing {class_name}.{method_name}")


def test_keyboard_busy_path_queues_normal_command() -> None:
    source = method_source(Path("app.py"), "MainWindow", "submit_text")
    assert "self.queue_worker(" in source
    assert 'source="keyboard"' in source
    assert "is_live_operation_status_query" in source
    assert "is_live_operation_cancel_query" in source


def test_queue_worker_keeps_runtime_callable_in_memory() -> None:
    source = method_source(Path("app.py"), "MainWindow", "queue_worker")
    assert "self.task_orchestrator.enqueue(" in source
    assert "self._pending_worker_jobs[record.task_id]" in source
    assert "WORKER_QUEUED" in source


def test_handoff_uses_qthread_finished_for_all_terminal_paths() -> None:
    source = method_source(Path("app.py"), "MainWindow", "run_worker")
    assert "start_next(_queued_task_id)" in source
    assert "self.worker.finished.connect(self._run_next_queued_worker)" in source
    assert "return True" in source


def test_restart_restored_metadata_is_not_executed_without_callable() -> None:
    source = method_source(Path("app.py"), "MainWindow", "_run_next_queued_worker")
    assert "job is None" in source
    assert "_pending_worker_jobs" in source


def test_voice_submit_path_does_not_use_normal_queue_worker() -> None:
    source = method_source(Path("app.py"), "MainWindow", "submit_local_command")
    assert "queue_worker(" not in source
