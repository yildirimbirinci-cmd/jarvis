from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic

from artmach_assistant.core.background_analysis_queue import BackgroundAnalysisQueue
from artmach_assistant.core.dependency_reindex_queue import DependencyReindexQueue
from artmach_assistant.core.incremental_index_queue import IncrementalIndexQueue
from artmach_assistant.core.workspace_watch import WorkspaceChange


def test_dependency_queue_does_not_start_for_empty_or_invalid_batches() -> None:
    queue = DependencyReindexQueue(lambda _paths: None, batch_wait_seconds=0.05)

    assert queue.submit([]) == 0
    assert queue.submit([None, object()]) == 0  # type: ignore[list-item]
    assert not queue.is_running


def test_background_queue_handles_invalid_timing_values() -> None:
    queue = BackgroundAnalysisQueue(idle_seconds=float("nan"), check_interval=float("inf"))

    assert queue._idle_seconds == 90.0
    assert queue._check_interval == 1.0
    queue.stop()


def test_background_queue_rejects_duplicate_key_while_callback_is_running() -> None:
    started = Event()
    release = Event()
    completed = Event()

    def callback() -> None:
        started.set()
        release.wait(2.0)
        completed.set()

    queue = BackgroundAnalysisQueue(idle_seconds=5.0, check_interval=0.25)
    queue._last_activity = monotonic() - 10.0

    assert queue.submit("semantic-scan", callback)
    assert started.wait(2.0)
    assert not queue.submit("semantic-scan", lambda: None)
    release.set()
    assert completed.wait(2.0)
    queue.stop()


def test_incremental_queue_rejects_blank_path_and_normalizes_blank_previous_path() -> None:
    received: list[WorkspaceChange] = []
    completed = Event()

    def callback(changes: list[WorkspaceChange]) -> None:
        received.extend(changes)
        completed.set()

    queue = IncrementalIndexQueue(callback, batch_wait_seconds=0.05)
    queue.submit(
        [
            WorkspaceChange("modified", "   "),
            WorkspaceChange("modified", Path("valid.py"), "   "),
        ]
    )

    assert completed.wait(2.0)
    assert queue.flush(timeout=1.0)
    queue.stop(drain=False)
    assert received == [WorkspaceChange("modified", Path("valid.py"), None)]
