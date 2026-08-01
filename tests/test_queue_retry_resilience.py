from __future__ import annotations

from pathlib import Path
from threading import Event
from time import monotonic, sleep

from artmach_assistant.core.background_analysis_queue import BackgroundAnalysisQueue
from artmach_assistant.core.dependency_reindex_queue import DependencyReindexQueue
from artmach_assistant.core.incremental_index_queue import IncrementalIndexQueue
from artmach_assistant.core.workspace_watch import WorkspaceChange, WorkspaceWatchService


def wait_for(predicate, timeout=2.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.02)
    return predicate()


def test_incremental_queue_retries_failed_batch():
    calls = []
    done = Event()

    def callback(changes):
        calls.append(tuple(changes))
        if len(calls) == 1:
            raise RuntimeError('temporary')
        done.set()

    queue = IncrementalIndexQueue(callback, batch_wait_seconds=0.05)
    queue.submit([WorkspaceChange('modified', Path('a.py'))])
    assert done.wait(2.0)
    queue.stop(drain=False)
    assert len(calls) >= 2


def test_dependency_queue_retries_failed_batch(tmp_path):
    calls = []
    done = Event()

    def callback(paths):
        calls.append(paths)
        if len(calls) == 1:
            raise RuntimeError('temporary')
        done.set()

    queue = DependencyReindexQueue(callback, batch_wait_seconds=0.05)
    queue.submit([tmp_path / 'a.py'])
    assert done.wait(2.0)
    queue.stop(drain=False)
    assert len(calls) >= 2


def test_background_queue_retries_failed_job():
    calls = []
    done = Event()

    def callback():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('temporary')
        done.set()

    queue = BackgroundAnalysisQueue(idle_seconds=5.0, check_interval=0.25)
    queue._last_activity = monotonic() - 10.0
    assert queue.submit('job', callback)
    assert done.wait(2.0)
    queue.stop()
    assert len(calls) >= 2


def test_workspace_watch_requeues_failed_dispatch():
    calls = []

    def callback(changes):
        calls.append(tuple(changes))
        if len(calls) == 1:
            raise RuntimeError('temporary')

    watcher = WorkspaceWatchService(callback, debounce_seconds=0.1)
    change = WorkspaceChange('modified', Path('a.py'))
    watcher._pending = {'a.py': change}
    watcher._last_change_at = 0.0
    watcher._flush(force=True)
    assert watcher._pending
    watcher._flush(force=True)
    assert not watcher._pending
    assert len(calls) == 2


def test_incremental_queue_handles_non_finite_timing_values():
    calls = []
    done = Event()

    def callback(changes):
        calls.append(tuple(changes))
        done.set()

    queue = IncrementalIndexQueue(callback, batch_wait_seconds=float("inf"))
    queue.submit([WorkspaceChange("modified", Path("safe.py"))])
    assert done.wait(2.0)
    assert queue.flush(timeout=float("nan"))
    queue.stop(drain=False)
    assert len(calls) == 1
