from __future__ import annotations

from pathlib import Path
from threading import Event

from artmach_assistant.core.background_analysis_queue import BackgroundAnalysisQueue
from artmach_assistant.core.dependency_reindex_queue import DependencyReindexQueue


def test_dependency_queue_keeps_items_before_broken_generator(tmp_path: Path) -> None:
    batches: list[tuple[Path, ...]] = []

    def paths():
        yield tmp_path / "a.py"
        yield tmp_path / "b.py"
        raise RuntimeError("broken source")

    queue = DependencyReindexQueue(batches.append, batch_wait_seconds=0.01)
    assert queue.submit(paths()) == 2
    assert queue.flush(2.0)
    queue.stop()
    assert {item.name for item in batches[0]} == {"a.py", "b.py"}


def test_dependency_queue_status_failure_does_not_kill_worker(tmp_path: Path, monkeypatch) -> None:
    from artmach_assistant.core import dependency_reindex_queue as module

    monkeypatch.setattr(module.DependencyReindexQueue, "_safe_status", staticmethod(lambda *a, **k: None))
    batches: list[tuple[Path, ...]] = []
    queue = DependencyReindexQueue(batches.append, batch_wait_seconds=0.01)
    assert queue.submit(tmp_path / "a.py") == 1
    assert queue.flush(2.0)
    queue.stop()
    assert batches


def test_background_analysis_rejects_broken_or_huge_keys() -> None:
    class Broken:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    queue = BackgroundAnalysisQueue(idle_seconds=0, check_interval=0.01)
    try:
        for key in (Broken(), "x" * 513):
            try:
                queue.submit(key, lambda: None)  # type: ignore[arg-type]
            except ValueError:
                pass
            else:
                raise AssertionError("invalid key accepted")
    finally:
        queue.stop()


def test_background_analysis_survives_status_failures(monkeypatch) -> None:
    from artmach_assistant.core import background_analysis_queue as module

    monkeypatch.setattr(module.BackgroundAnalysisQueue, "_safe_status", staticmethod(lambda *a, **k: None))
    called = Event()
    queue = BackgroundAnalysisQueue(idle_seconds=0, check_interval=0.01)
    assert queue.submit("job", called.set)
    assert called.wait(1.0)
    queue.stop()
