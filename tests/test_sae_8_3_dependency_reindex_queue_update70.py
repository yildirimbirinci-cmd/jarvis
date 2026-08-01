from pathlib import Path

import artmach_assistant.core.dependency_reindex_queue as queue_module
from artmach_assistant.core.dependency_reindex_queue import DependencyReindexQueue
from artmach_assistant.core.service_status import ServiceStatusRegistry


def _fresh_registry(monkeypatch):
    registry = ServiceStatusRegistry()
    monkeypatch.setattr(queue_module, "service_status_registry", registry)
    return registry


def test_submit_records_queued_work_before_starting_worker(monkeypatch, tmp_path: Path) -> None:
    registry = _fresh_registry(monkeypatch)
    queue = DependencyReindexQueue(lambda _paths: None)
    observed: list[tuple[int, int]] = []

    def fake_start() -> None:
        observed.append((registry.snapshot("dependency_reindex")["queued"], queue._queue.qsize()))

    monkeypatch.setattr(queue, "start", fake_start)

    assert queue.submit([tmp_path / "module.py"]) == 1
    assert observed == [(1, 1)]


def test_stop_without_drain_discards_pending_status_without_processing(monkeypatch, tmp_path: Path) -> None:
    registry = _fresh_registry(monkeypatch)
    queue = DependencyReindexQueue(lambda _paths: None)
    monkeypatch.setattr(queue, "start", lambda: None)

    assert queue.submit([tmp_path / "a.py", tmp_path / "b.py"]) == 2
    assert registry.snapshot("dependency_reindex")["queued"] == 2

    queue.stop(drain=False)

    status = registry.snapshot("dependency_reindex")
    assert status["queued"] == 0
    assert status["processed"] == 0
    assert status["failed"] == 0
    assert status["state"] == "stopped"
    assert queue._queue.unfinished_tasks == 0
