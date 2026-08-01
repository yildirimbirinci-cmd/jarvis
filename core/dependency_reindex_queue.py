from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
import math
import os
from pathlib import Path
from queue import Empty, Queue
from threading import Event, RLock, Thread, current_thread
from time import monotonic

from artmach_assistant.core.service_status import service_status_registry


ReindexCallback = Callable[[tuple[Path, ...]], None]


class DependencyReindexQueue:
    """Deduplicates and batches dependency-driven reindex requests."""

    SERVICE_NAME = "dependency_reindex"

    def __init__(self, callback: ReindexCallback, *, batch_wait_seconds: float = 0.20) -> None:
        if not callable(callback):
            raise TypeError("Yeniden indeksleme callback'i çağrılabilir olmalıdır.")
        self._callback = callback
        self._batch_wait_seconds = self._safe_seconds(batch_wait_seconds, default=0.20, minimum=0.05)
        self._queue: Queue[Path | None] = Queue()
        self._queued: set[str] = set()
        self._last_batch: tuple[Path, ...] = ()
        self._stop_event = Event()
        self._idle_event = Event()
        self._idle_event.set()
        self._lock = RLock()
        self._thread: Thread | None = None
        self._safe_status("ensure", self.SERVICE_NAME)

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    @property
    def last_batch(self) -> tuple[Path, ...]:
        with self._lock:
            return self._last_batch

    def start(self) -> None:
        with self._lock:
            thread = self._thread
            if thread and thread.is_alive():
                return
            self._stop_event.clear()
            thread = Thread(target=self._run, name="JarvisDependencyReindex", daemon=True)
            self._thread = thread
            thread.start()
        self._safe_status("set_state", self.SERVICE_NAME, "idle", "Bağımlılık yeniden indeksleme kuyruğu hazır.")

    def submit(self, paths: Iterable[Path] | Path | str) -> int:
        accepted = 0
        with self._lock:
            if self._stop_event.is_set():
                return 0
            for path in self._iter_candidates(paths):
                normalized = self._normalize_path(path)
                if normalized is None:
                    continue
                key = self._path_key(normalized)
                if key in self._queued:
                    continue
                self._queued.add(key)
                self._queue.put(normalized)
                accepted += 1
            if accepted:
                self._idle_event.clear()
                self._safe_status("queued", self.SERVICE_NAME, accepted)
                self.start()
        return accepted

    def flush(self, timeout: float = 3.0) -> bool:
        seconds = self._safe_seconds(timeout, default=3.0, minimum=0.0)
        if self._queue.unfinished_tasks == 0:
            return True
        return self._idle_event.wait(seconds)

    def stop(self, *, drain: bool = True) -> None:
        with self._lock:
            thread = self._thread
        if drain:
            self.flush(timeout=3.0)
        self._stop_event.set()
        if thread and thread.is_alive():
            self._queue.put(None)
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=3.0)
        if thread and thread.is_alive():
            self._safe_status(
                "set_state", self.SERVICE_NAME, "stopping",
                "Çalışan yeniden indeksleme tamamlanınca kuyruk durdurulacak.",
            )
            return
        self._finalize_stopped(thread)

    def _run(self) -> None:
        worker = current_thread()
        try:
            self._run_loop()
        finally:
            if self._stop_event.is_set():
                self._finalize_stopped(worker)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                first = self._queue.get(timeout=0.5)
            except Empty:
                continue
            if first is None:
                self._queue.task_done()
                self._update_idle_event()
                return

            batch: set[Path] = {first}
            consumed = 1
            while not self._stop_event.is_set():
                try:
                    item = self._queue.get(timeout=self._batch_wait_seconds)
                except Empty:
                    break
                if item is None:
                    self._queue.task_done()
                    self._stop_event.set()
                    break
                batch.add(item)
                consumed += 1

            ordered = tuple(sorted(batch, key=self._safe_sort_key))
            with self._lock:
                for item in batch:
                    self._queued.discard(self._path_key(item))
            try:
                self._safe_status(
                    "set_state", self.SERVICE_NAME, "running",
                    f"{len(ordered)} etkilenen modül yeniden indeksleniyor.",
                )
                self._callback(ordered)
                with self._lock:
                    self._last_batch = ordered
                self._safe_status(
                    "completed", self.SERVICE_NAME, len(ordered),
                    "Etkilenen modüllerin indeksi güncellendi.",
                )
            except Exception as exc:
                self._safe_status("failed", self.SERVICE_NAME, exc, len(ordered))
                if not self._stop_event.is_set():
                    retried = 0
                    with self._lock:
                        for item in ordered:
                            key = self._path_key(item)
                            if key in self._queued:
                                continue
                            self._queued.add(key)
                            self._queue.put(item)
                            retried += 1
                    if retried:
                        self._safe_status("queued", self.SERVICE_NAME, retried)
            finally:
                for _ in range(consumed):
                    self._queue.task_done()
                self._update_idle_event()
                if self._queue.unfinished_tasks:
                    self._stop_event.wait(self._batch_wait_seconds)

    def _finalize_stopped(self, worker: Thread | None) -> None:
        with self._lock:
            if self._thread is not worker and self._thread is not None:
                return
            if self._thread is worker:
                self._thread = None
        discarded = self._drain()
        if discarded:
            self._safe_status("discarded", self.SERVICE_NAME, discarded)
        self._stop_event.clear()
        with self._lock:
            self._queued.clear()
            self._last_batch = ()
        self._idle_event.set()
        self._safe_status(
            "set_state", self.SERVICE_NAME, "stopped",
            "Bağımlılık yeniden indeksleme kuyruğu durduruldu.",
        )

    def _drain(self) -> int:
        discarded = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except Empty:
                break
            if item is not None:
                discarded += 1
                with self._lock:
                    self._queued.discard(self._path_key(item))
            self._queue.task_done()
        self._update_idle_event()
        return discarded

    def _update_idle_event(self) -> None:
        if self._queue.unfinished_tasks == 0:
            self._idle_event.set()
        else:
            self._idle_event.clear()

    @staticmethod
    def _iter_candidates(paths: Iterable[Path] | Path | str) -> Iterator[object]:
        if isinstance(paths, (str, Path)):
            yield paths
            return
        try:
            iterator = iter(paths)
        except TypeError:
            return
        while True:
            try:
                yield next(iterator)
            except StopIteration:
                return
            except Exception:
                return

    @staticmethod
    def _normalize_path(path: object) -> Path | None:
        try:
            return Path(path).expanduser().resolve(strict=False)  # type: ignore[arg-type]
        except (OSError, TypeError, ValueError, RuntimeError):
            return None

    @staticmethod
    def _safe_sort_key(path: Path) -> str:
        try:
            return str(path).casefold()
        except Exception:
            return ""

    @staticmethod
    def _safe_seconds(value: object, *, default: float, minimum: float) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            result = default
        if not math.isfinite(result):
            result = default
        return max(minimum, result)

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            return os.path.normcase(os.path.normpath(str(path)))
        except Exception:
            return repr(path)

    @staticmethod
    def _safe_status(method: str, *args: object, **kwargs: object) -> None:
        try:
            callback = getattr(service_status_registry, method)
            callback(*args, **kwargs)
        except Exception:
            pass
