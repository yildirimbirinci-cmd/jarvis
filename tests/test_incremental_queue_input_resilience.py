from pathlib import Path
from threading import Event

from artmach_assistant.core.incremental_index_queue import IncrementalIndexQueue
from artmach_assistant.core.workspace_watch import WorkspaceChange


def test_incremental_queue_ignores_invalid_changes_without_killing_worker():
    received = []
    done = Event()

    def callback(changes):
        received.extend(changes)
        done.set()

    queue = IncrementalIndexQueue(callback, batch_wait_seconds=0.05)
    queue.submit([
        None,
        object(),
        WorkspaceChange("unknown", Path("ignored.py")),
        WorkspaceChange(" MODIFIED ", "valid.py"),
    ])

    assert done.wait(2.0)
    assert queue.flush(timeout=1.0)
    assert queue.is_running
    queue.stop(drain=False)

    assert received == [WorkspaceChange("modified", Path("valid.py"))]


def test_incremental_queue_does_not_start_for_fully_invalid_batch():
    queue = IncrementalIndexQueue(lambda changes: None, batch_wait_seconds=0.05)
    queue.submit([None, object(), WorkspaceChange("unknown", Path("ignored.py"))])

    assert not queue.is_running
    assert queue.flush(timeout=0.1)
