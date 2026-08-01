from pathlib import Path
from threading import Event

from artmach_assistant.core.incremental_index_queue import IncrementalIndexQueue
from artmach_assistant.core.service_status import ServiceStatusRegistry
from artmach_assistant.core.workspace_watch import WorkspaceChange


def test_incremental_queue_preserves_items_from_partially_failing_generator():
    received = []
    done = Event()

    def source():
        yield WorkspaceChange("modified", Path("valid.py"))
        raise RuntimeError("generator failed")

    def callback(changes):
        received.extend(changes)
        done.set()

    queue = IncrementalIndexQueue(callback, batch_wait_seconds=0.05)
    queue.submit(source())

    assert done.wait(2.0)
    assert queue.flush(timeout=1.0)
    queue.stop(drain=False)
    assert received == [WorkspaceChange("modified", Path("valid.py"))]


def test_incremental_queue_flush_uses_completion_signal():
    release = Event()
    entered = Event()

    def callback(changes):
        entered.set()
        release.wait(2.0)

    queue = IncrementalIndexQueue(callback, batch_wait_seconds=0.05)
    queue.submit([WorkspaceChange("modified", Path("valid.py"))])
    assert entered.wait(1.0)
    assert not queue.flush(timeout=0.05)
    release.set()
    assert queue.flush(timeout=1.0)
    queue.stop(drain=False)


def test_service_status_handles_broken_error_string():
    class BrokenError(Exception):
        def __str__(self):
            raise RuntimeError("broken str")

    registry = ServiceStatusRegistry()
    registry.failed("worker", BrokenError(), 1)
    snapshot = registry.snapshot("worker")

    assert snapshot["state"] == "error"
    assert snapshot["last_error"] == "BrokenError"
    assert snapshot["details"]["error_type"] == "BrokenError"
