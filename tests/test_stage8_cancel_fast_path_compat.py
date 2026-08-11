from __future__ import annotations

import ast
from pathlib import Path


def _method(name: str) -> str:
    text = Path('app.py').read_text(encoding='utf-8')
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'MainWindow':
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    segment = ast.get_source_segment(text, child)
                    assert segment is not None
                    return segment
    raise AssertionError(name)


def test_submit_preserves_legacy_worker_fast_path_and_start_race_cover() -> None:
    source = _method('submit')
    assert 'task_in_flight = worker_running or bool(self._active_task_id)' in source
    assert 'if task_in_flight and not worker_running and (live_status or live_cancel):' in source
    assert 'worker_running = True' in source
    assert 'if worker_running and (live_status or live_cancel):' in source
    assert source.index('if worker_running and (live_status or live_cancel):') < source.index('self.submit_text(text)')


def test_submit_text_preserves_legacy_worker_fast_path_and_start_race_cover() -> None:
    source = _method('submit_text')
    assert 'task_in_flight = worker_running or bool(self._active_task_id)' in source
    assert 'if task_in_flight and not worker_running and (live_status or live_cancel):' in source
    assert 'worker_running = True' in source
    assert 'if (live_status or live_cancel) and worker_running:' in source
    assert source.index('if (live_status or live_cancel) and worker_running:') < source.index('active = self.task_orchestrator.active')
