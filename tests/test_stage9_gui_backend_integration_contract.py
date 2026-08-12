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


def test_natural_language_keyboard_command_uses_engine_handle_in_idle_and_queue_paths() -> None:
    source = _method("submit_text")
    assert source.count("lambda: self.engine.handle(text)") >= 2
    assert "self.queue_worker(" in source
    assert "self.run_worker(" in source
    assert "self.on_answer" in source


def test_live_status_and_cancel_stay_on_gui_fast_path_while_busy() -> None:
    source = _method("submit_text")
    assert "build_live_status_answer(self.engine, None)" in source
    assert "self.cancel_active_task()" in source
    assert 'route="lock_free_snapshot"' in source
    assert 'route="cancel_fast_path"' in source


def test_busy_check_does_not_read_orchestrator_active_lock() -> None:
    source = _method("busy")
    assert "self.task_orchestrator.active" not in source
    assert "self._active_task_id" in source
    assert "self._active_intent" in source


def test_worker_progress_timer_does_not_read_orchestrator_active_lock() -> None:
    source = _method("run_worker")
    assert "if self._active_task_id != record.task_id:" in source
    assert "active = self.task_orchestrator.active" not in source


def test_backend_success_callback_failure_is_contained_in_gui_thread() -> None:
    source = _method("run_worker")
    assert '"GUI_CALLBACK_FAILED"' in source
    assert "try:\n                callback(result)" in source
    assert 'self.on_error(f"Arayüz yanıt işleme hatası: {exc}")' in source
    assert "QTimer.singleShot(0, self._run_next_queued_worker)" in source


def test_backend_error_callback_failure_is_contained_and_fifo_handoff_survives() -> None:
    source = _method("run_worker")
    assert '"GUI_ERROR_CALLBACK_FAILED"' in source
    assert "handler = error_callback or self.on_error" in source
    assert "self.task_orchestrator.finish(record.task_id, error=str(error), cancelled=cancelled)" in source
    assert "QTimer.singleShot(0, self._run_next_queued_worker)" in source


def test_restart_restored_queue_metadata_is_not_executed_without_runtime_callable() -> None:
    source = _method("_run_next_queued_worker")
    assert "self._pending_worker_jobs.get(next_record.task_id)" in source
    assert "if job is None:" in source
    assert "return" in source


def test_stage8_cancel_contract_remains_intact() -> None:
    source = _method("cancel_active_task")
    assert 'cancel_latest_pending("kullanıcı iptali")' in source
    assert "self._pending_worker_jobs.pop(removed.task_id, None)" in source


def test_idle_live_status_does_not_fall_through_to_engine_handle() -> None:
    source = _method("submit_text")
    fast = source.index("if live_status or live_cancel:")
    engine = source.index("lambda: self.engine.handle(text)")
    assert fast < engine
    assert "build_live_status_answer(self.engine, None)" in source[:engine]


def test_idle_cancel_does_not_fall_through_to_engine_handle() -> None:
    source = _method("submit_text")
    fast = source.index("if live_status or live_cancel:")
    engine = source.index("lambda: self.engine.handle(text)")
    assert fast < engine
    assert "self.cancel_active_task()" in source[:engine]


def test_keyboard_submission_never_reads_orchestrator_active_lock() -> None:
    source = _method("submit_text")
    assert "self.task_orchestrator.active" not in source
    assert '"TASK_SNAPSHOT_READ"' not in source
