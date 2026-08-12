from __future__ import annotations

import ast
from pathlib import Path


def _app_source() -> str:
    return (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def _method(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"method not found: {name}")


def test_live_status_branch_is_checked_without_busy_lock() -> None:
    source = _app_source()
    tree = ast.parse(source)
    method = _method(tree, "submit_text")
    text = ast.get_source_segment(source, method) or ""
    live_branch = "if (live_status or live_cancel) and worker_running:"
    assert live_branch in text
    assert "self.task_orchestrator.active" not in text
    assert text.index(live_branch) < text.index("if self.busy():")
    assert "if self.busy() and (live_status or live_cancel):" not in text


def test_close_event_uses_non_blocking_shutdown_polling() -> None:
    source = _app_source()
    tree = ast.parse(source)
    close_text = ast.get_source_segment(source, _method(tree, "closeEvent")) or ""
    poll_text = ast.get_source_segment(source, _method(tree, "_poll_shutdown_workers")) or ""
    start_text = ast.get_source_segment(source, _method(tree, "_start_async_shutdown")) or ""

    assert "self._start_async_shutdown()" in close_text
    assert "event.ignore()" in close_text
    assert ".wait(" not in close_text
    assert "QTimer.singleShot(100, self._poll_shutdown_workers)" in poll_text
    assert "time.monotonic() + 3.0" in start_text
