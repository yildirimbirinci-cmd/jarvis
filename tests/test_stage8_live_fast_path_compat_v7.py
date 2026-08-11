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


def test_submit_has_race_cover_and_task_in_flight_fast_path() -> None:
    source = _method("submit")
    assert "task_in_flight = worker_running or bool(self._active_task_id)" in source
    assert "if task_in_flight and not worker_running and (live_status or live_cancel):" in source
    assert "if task_in_flight and (live_status or live_cancel):" in source


def test_submit_text_has_race_cover_and_task_in_flight_fast_path() -> None:
    source = _method("submit_text")
    assert "task_in_flight = worker_running or bool(self._active_task_id)" in source
    assert "if task_in_flight and not worker_running and (live_status or live_cancel):" in source
    assert "if (live_status or live_cancel) and task_in_flight:" in source
