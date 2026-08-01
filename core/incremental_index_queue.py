from __future__ import annotations

from collections.abc import Callable, Iterable
import math
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread, current_thread

from artmach_assistant.core.service_status import service_status_registry
from artmach_assistant.core.workspace_watch import (
    WorkspaceChange,
    merge_workspace_changes,
    workspace_change_key,
)


class IncrementalIndexQueue:
    """Applies workspace changes away from the UI and voice threads."""

    def __init__(
        self,
        callback: Callable[[list[WorkspaceChange]], None],
        batch_wait_seconds: float = 0.20,
    ) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback
        try:
            wait = float(batch_wait_seconds)
        except (TypeError, ValueError, OverflowError):
            wait = 0.20
        if not math.isfinite(wait):
            wait = 0.20
        self._batch_wait_seconds = max(0.05, wait)
        self._queue: Queue[WorkspaceChange | None] = Queue()
        self._stop_event = Event()
        self._idle_event = Event()
        self._idle_event.set()
        self._lifecycle_lock = Lock()
        self._thread: Thread | None = None
        self._safe_status("ensure", "incremental_index")

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread and thread.is_alive():
                return
            self._thread = None
            self._stop_event.clear()
            thread = Thread(
                target=self._run,
                name="JarvisIncrementalIndex",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        self._safe_status("set_state", "incremental_index", "idle", "Artımlı indeks kuyruğu hazır.")

    def submit(self, changes: Iterable[WorkspaceChange] | None) -> None:
        if changes is None:
            return
        try:
            iterator = iter(changes)
        except TypeError:
            return
        accepted: list[WorkspaceChange] = []
        try:
            for change in iterator:
                if not isinstance(change, WorkspaceChange):
                    continue
                try:
                    kind = str(change.kind).strip().casefold()
                except Exception:
                    continue
                if kind not in {"created", "modified", "deleted"}:
                    continue
                raw_path = change.path
                if isinstance(raw_path, str) and not raw_path.strip():
                    continue
                raw_previous = change.previous_path
                if isinstance(raw_previous, str) and not raw_previous.strip():
                    raw_previous = None
                try:
                    path = Path(raw_path)
                    previous_path = Path(raw_previous) if raw_previous is not None else None
                except (TypeError, ValueError, OSError):
                    continue
                accepted.append(WorkspaceChange(kind, path, previous_path))
        except Exception:
            # Preserve valid rows already yielded by a partially failing generator.
            pass
        if not accepted:
            return
        self.start()
        self._idle_event.clear()
        self._safe_status("queued", "incremental_index", len(accepted))
        for change in accepted:
            self._queue.put(change)

    def flush(self, timeout: float = 3.0) -> bool:
        try:
            seconds = float(timeout)
        except (TypeError, ValueError, OverflowError):
            seconds = 3.0
        if not math.isfinite(seconds):
            seconds = 3.0
        if self._queue.unfinished_tasks == 0:
            self._idle_event.set()
            return True
        return self._idle_event.wait(max(0.0, seconds)) and self._queue.unfinished_tasks == 0

    def stop(self, *, drain: bool = True) -> None:
        with self._lifecycle_lock:
            thread = self._thread
        if drain:
            self.flush(timeout=3.0)
        self._stop_event.set()
        if thread and thread.is_alive():
            self._queue.put(None)
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=3.0)
        with self._lifecycle_lock:
            current = self._thread
            if current is None or not current.is_alive():
                self._thread = None
                discarded = self._drain()
                if discarded:
                    self._safe_status(
                        "discarded",
                        "incremental_index",
                        discarded,
                        f"{discarded} bekleyen indeks değişikliği iptal edildi.",
                    )
                self._stop_event.clear()
                state = "stopped"
                message = "Artımlı indeks kuyruğu durduruldu."
            else:
                state = "stopping"
                message = "Artımlı indeks kuyruğunun durması bekleniyor."
        self._safe_status("set_state", "incremental_index", state, message)

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    first = self._queue.get(timeout=0.5)
                except Empty:
                    continue
                if first is None:
                    self._queue.task_done()
                    self._update_idle_event()
                    return
                try:
                    first_key = workspace_change_key(first.path)
                except Exception:
                    self._queue.task_done()
                    self._update_idle_event()
                    continue
                pending: dict[str, WorkspaceChange] = {first_key: first}
                consumed_count = 1
                while not self._stop_event.is_set():
                    try:
                        item = self._queue.get(timeout=self._batch_wait_seconds)
                    except Empty:
                        break
                    if item is None:
                        self._queue.task_done()
                        self._stop_event.set()
                        break
                    consumed_count += 1
                    try:
                        key = workspace_change_key(item.path)
                        merged = merge_workspace_changes(pending.get(key), item)
                    except Exception:
                        continue
                    if merged is None:
                        pending.pop(key, None)
                    else:
                        pending[key] = merged
                retry: list[WorkspaceChange] = []
                try:
                    if pending:
                        changes = sorted(pending.values(), key=self._change_sort_key)
                        try:
                            self._safe_status(
                                "set_state",
                                "incremental_index",
                                "running",
                                f"{len(changes)} dosya değişikliği işleniyor.",
                            )
                            self._callback(changes)
                            self._safe_status(
                                "completed",
                                "incremental_index",
                                len(changes),
                                "Artımlı indeks güncel.",
                            )
                        except Exception as exc:
                            self._safe_status("failed", "incremental_index", exc, len(changes))
                            if not self._stop_event.is_set():
                                retry = changes
                finally:
                    for change in retry:
                        self._queue.put(change)
                    if retry:
                        self._safe_status("queued", "incremental_index", len(retry))
                    for _ in range(consumed_count):
                        self._queue.task_done()
                    self._update_idle_event()
                    if retry:
                        self._stop_event.wait(self._batch_wait_seconds)
        finally:
            with self._lifecycle_lock:
                if self._thread is current_thread():
                    self._thread = None

    @staticmethod
    def _change_sort_key(change: WorkspaceChange) -> str:
        try:
            return str(change.path).casefold()
        except Exception:
            return ""

    def _update_idle_event(self) -> None:
        if self._queue.unfinished_tasks == 0:
            self._idle_event.set()
        else:
            self._idle_event.clear()

    @staticmethod
    def _safe_status(method: str, *args: object, **kwargs: object) -> None:
        try:
            callback = getattr(service_status_registry, method)
            callback(*args, **kwargs)
        except Exception:
            pass

    def _drain(self) -> int:
        discarded = 0
        while True:
            try:
                item = self._queue.get_nowait()
                if item is not None:
                    discarded += 1
                self._queue.task_done()
            except Empty:
                self._idle_event.set()
                return discarded
