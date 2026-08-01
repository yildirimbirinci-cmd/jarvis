from __future__ import annotations

from pathlib import Path
from threading import Event

from artmach_assistant.core.dependency_reindex_queue import DependencyReindexQueue
from artmach_assistant.core.incremental_index_queue import IncrementalIndexQueue
from artmach_assistant.core.service_status import service_status_registry
from artmach_assistant.core.workspace_watch import WorkspaceChange


def test_dependency_retry_restores_queued_status(tmp_path: Path) -> None:
    second_attempt = Event()
    queued_seen: list[int] = []
    attempts = 0

    def callback(_batch: tuple[Path, ...]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        queued_seen.append(service_status_registry.snapshot("dependency_reindex")["queued"])
        second_attempt.set()

    queue = DependencyReindexQueue(callback, batch_wait_seconds=0.05)
    queue.submit(tmp_path / "module.py")

    assert second_attempt.wait(2.0)
    assert queue.flush(timeout=2.0)
    queue.stop(drain=False)

    assert queued_seen == [1]


def test_incremental_retry_restores_queued_status() -> None:
    second_attempt = Event()
    queued_seen: list[int] = []
    attempts = 0

    def callback(_changes: list[WorkspaceChange]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        queued_seen.append(service_status_registry.snapshot("incremental_index")["queued"])
        second_attempt.set()

    queue = IncrementalIndexQueue(callback, batch_wait_seconds=0.05)
    queue.submit([WorkspaceChange("modified", Path("module.py"))])

    assert second_attempt.wait(2.0)
    assert queue.flush(timeout=2.0)
    queue.stop(drain=False)

    assert queued_seen == [1]
