from pathlib import Path

from artmach_assistant.core.incremental_index_queue import IncrementalIndexQueue
from artmach_assistant.core.service_status import ServiceStatusRegistry, service_status_registry
from artmach_assistant.core.workspace_watch import WorkspaceChange


def test_discarded_reduces_queued_without_marking_success_or_failure() -> None:
    registry = ServiceStatusRegistry()
    registry.queued("worker", 3)

    registry.discarded("worker", 2, "two jobs cancelled")

    status = registry.snapshot("worker")
    assert status["queued"] == 1
    assert status["processed"] == 0
    assert status["failed"] == 0
    assert status["message"] == "two jobs cancelled"


def test_stop_without_drain_clears_discarded_queue_accounting() -> None:
    name = "incremental_index"
    before = service_status_registry.snapshot(name)
    queue = IncrementalIndexQueue(lambda changes: None)
    queued = [
        WorkspaceChange("modified", Path("core/a.py")),
        WorkspaceChange("deleted", Path("core/b.py")),
    ]
    for change in queued:
        queue._queue.put(change)
    service_status_registry.queued(name, len(queued))

    queue.stop(drain=False)

    after = service_status_registry.snapshot(name)
    assert queue._queue.unfinished_tasks == 0
    assert after["queued"] == before["queued"]
    assert after["processed"] == before["processed"]
    assert after["failed"] == before["failed"]
    assert after["state"] == "stopped"
