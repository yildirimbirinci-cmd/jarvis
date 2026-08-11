from __future__ import annotations

import ast
from pathlib import Path


def method_source(method_name: str) -> str:
    path = Path("app.py")
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    segment = ast.get_source_segment(text, child)
                    assert segment is not None
                    return segment
    raise AssertionError(f"missing MainWindow.{method_name}")


def test_submit_fast_path_treats_started_task_as_in_flight_before_qthread_reports_running() -> None:
    source = method_source("submit")
    assert "task_in_flight = worker_running or bool(self._active_task_id)" in source
    assert "if task_in_flight and (live_status or live_cancel):" in source


def test_submit_text_fast_path_treats_started_task_as_in_flight_before_qthread_reports_running() -> None:
    source = method_source("submit_text")
    assert "task_in_flight = worker_running or bool(self._active_task_id)" in source
    assert "if (live_status or live_cancel) and task_in_flight:" in source


def test_busy_covers_qthread_start_race_window() -> None:
    source = method_source("busy")
    assert "active_task_id = str(self._active_task_id or \"\").strip()" in source
    assert "if not worker_running and not active_task_id:" in source
