from __future__ import annotations

from threading import Event
from time import sleep

from artmach_assistant.core.background_refactoring_queue import (
    BackgroundRefactoringQueue,
    RefactoringJobState,
)


def test_stop_cancels_queued_jobs_and_releases_deduplication_key() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=30, check_interval=0.01)
    called = Event()
    first = queue.submit("same", lambda cancel: called.set())
    assert first is not None

    queue.stop(cancel_pending=True)

    assert first.state is RefactoringJobState.CANCELLED
    assert first.finished_at is not None
    assert not called.is_set()

    replacement = queue.submit("same", lambda cancel: called.set())
    assert replacement is not None
    queue._idle_seconds = 0  # Exercise restart without waiting for user idle time.
    queue.mark_activity()
    assert queue.wait("same", 1).state is RefactoringJobState.COMPLETED
    queue.stop()
    assert called.is_set()


def test_graceful_stop_preserves_dequeued_job_for_restart() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=30, check_interval=0.01)
    called = Event()
    job = queue.submit("preserve", lambda cancel: called.set())
    assert job is not None

    # Give the worker time to dequeue the job and wait for the idle window.
    sleep(0.05)
    queue.stop(cancel_pending=False)

    assert job.state is RefactoringJobState.QUEUED
    assert not called.is_set()

    queue._idle_seconds = 0
    queue.start()
    assert queue.wait("preserve", 1).state is RefactoringJobState.COMPLETED
    queue.stop()
    assert called.is_set()


def test_stop_requests_running_job_cancellation_and_finishes_terminally() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0, check_interval=0.01)
    started = Event()

    def callback(cancel: Event) -> None:
        started.set()
        cancel.wait(1)

    job = queue.submit("running", callback)
    assert job is not None
    assert started.wait(1)

    queue.stop(cancel_pending=True)

    assert job.state is RefactoringJobState.CANCELLED
    assert job.finished_at is not None
