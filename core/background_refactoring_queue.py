from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from itertools import count
from queue import Empty, PriorityQueue
from threading import Event, RLock, Thread, current_thread
from time import monotonic
from typing import Any

from artmach_assistant.core.service_status import service_status_registry


class RefactoringPriority(IntEnum):
    HIGH = 0
    NORMAL = 10
    LOW = 20


class RefactoringJobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATES = {
    RefactoringJobState.COMPLETED,
    RefactoringJobState.FAILED,
    RefactoringJobState.CANCELLED,
}


@dataclass(frozen=True)
class RefactoringJobResult:
    """Read-only result produced by a background refactoring job.

    The queue deliberately does not apply edits. A callback may analyse the
    workspace or prepare a proposal, but user approval must happen elsewhere.
    """

    summary: str
    payload: Any = None


@dataclass
class RefactoringJob:
    key: str
    callback: Callable[[Event], RefactoringJobResult | Any]
    priority: RefactoringPriority = RefactoringPriority.NORMAL
    state: RefactoringJobState = RefactoringJobState.QUEUED
    submitted_at: float = field(default_factory=monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    result: RefactoringJobResult | None = None
    error: str = ""
    cancel_event: Event = field(default_factory=Event, repr=False)
    done_event: Event = field(default_factory=Event, repr=False)


class BackgroundRefactoringQueue:
    """Runs safe refactoring analysis/planning jobs while Jarvis is idle.

    Jobs are deduplicated by key, priority ordered and cooperatively
    cancellable. Callbacks receive an Event and should stop promptly when it is
    set. The queue never writes project files or auto-applies proposals.
    """

    SERVICE_NAME = "background_refactoring"
    MAX_KEY_LENGTH = 512

    def __init__(
        self,
        *,
        idle_seconds: float = 90.0,
        check_interval: float = 0.25,
    ) -> None:
        self._idle_seconds = self._safe_non_negative(idle_seconds, 90.0)
        self._check_interval = max(0.02, self._safe_non_negative(check_interval, 0.25))
        self._queue: PriorityQueue[tuple[int, int, str | None]] = PriorityQueue()
        self._jobs: dict[str, RefactoringJob] = {}
        self._active_keys: set[str] = set()
        self._sequence = count()
        self._last_activity = monotonic()
        self._stop_event = Event()
        self._lock = RLock()
        self._thread: Thread | None = None
        self._status_call("ensure", self.SERVICE_NAME)

    @staticmethod
    def _safe_non_negative(value: object, default: float) -> float:
        try:
            converted = float(value)
        except (TypeError, ValueError, OverflowError):
            converted = default
        if converted != converted or converted == float("inf"):
            converted = default
        return max(0.0, converted)

    @staticmethod
    def _safe_text(value: object, default: str = "") -> str:
        try:
            return str(value)
        except Exception:
            return default

    @classmethod
    def _normalize_key(cls, key: object) -> str:
        normalized = cls._safe_text(key).strip()
        if not normalized:
            raise ValueError("Refactoring iş anahtarı boş olamaz.")
        if len(normalized) > cls.MAX_KEY_LENGTH:
            raise ValueError("Refactoring iş anahtarı çok uzun.")
        return normalized

    @staticmethod
    def _status_call(method_name: str, *args: object, **kwargs: object) -> None:
        try:
            method = getattr(service_status_registry, method_name)
            method(*args, **kwargs)
        except Exception:
            # Service-status reporting is diagnostic. It must never terminate
            # the execution queue or change the job's real terminal state.
            return

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._thread = Thread(
                target=self._run,
                name="JarvisBackgroundRefactoring",
                daemon=True,
            )
            self._thread.start()
        self._status_call(
            "set_state",
            self.SERVICE_NAME,
            "idle",
            "Arka plan refactoring kuyruğu hazır.",
        )

    def mark_activity(self) -> None:
        with self._lock:
            self._last_activity = monotonic()

    def submit(
        self,
        key: str,
        callback: Callable[[Event], RefactoringJobResult | Any],
        *,
        priority: RefactoringPriority = RefactoringPriority.NORMAL,
    ) -> RefactoringJob | None:
        normalized = self._normalize_key(key)
        if not callable(callback):
            raise TypeError("Refactoring callback'i çağrılabilir olmalıdır.")
        try:
            normalized_priority = RefactoringPriority(priority)
        except (TypeError, ValueError) as exc:
            raise ValueError("Geçersiz refactoring önceliği.") from exc

        with self._lock:
            if self._stop_event.is_set() or normalized in self._active_keys:
                return None
            job = RefactoringJob(normalized, callback, normalized_priority)
            self._jobs[normalized] = job
            self._active_keys.add(normalized)
            self._queue.put((int(normalized_priority), next(self._sequence), normalized))
            self._status_call("queued", self.SERVICE_NAME, 1)
            self.start()
        return job

    def cancel(self, key: str) -> bool:
        try:
            normalized = self._normalize_key(key)
        except ValueError:
            return False
        with self._lock:
            job = self._jobs.get(normalized)
            if job is None or job.state in _TERMINAL_STATES:
                return False
            job.cancel_event.set()
            if job.state is RefactoringJobState.QUEUED:
                self._finish_locked(job, RefactoringJobState.CANCELLED)
                self._status_call(
                    "discarded", self.SERVICE_NAME, 1, f"{normalized} iptal edildi."
                )
            return True

    def get(self, key: str) -> RefactoringJob | None:
        try:
            normalized = self._normalize_key(key)
        except ValueError:
            return None
        with self._lock:
            return self._jobs.get(normalized)

    def wait(self, key: str, timeout: float = 3.0) -> RefactoringJob | None:
        job = self.get(key)
        if job is None or job.state in _TERMINAL_STATES:
            return job
        wait_seconds = self._safe_non_negative(timeout, 3.0)
        job.done_event.wait(wait_seconds)
        return job

    def stop(self, *, cancel_pending: bool = True) -> None:
        cancelled_keys: list[str] = []
        with self._lock:
            thread = self._thread
            self._stop_event.set()
            if cancel_pending:
                for key, job in self._jobs.items():
                    if job.state is RefactoringJobState.QUEUED:
                        job.cancel_event.set()
                        self._finish_locked(job, RefactoringJobState.CANCELLED)
                        cancelled_keys.append(key)
                    elif job.state is RefactoringJobState.RUNNING:
                        job.cancel_event.set()
            if thread is not None and thread.is_alive():
                self._queue.put((10**9, next(self._sequence), None))

        for key in cancelled_keys:
            self._status_call(
                "discarded", self.SERVICE_NAME, 1, f"{key} iptal edildi."
            )

        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=3.0)

        with self._lock:
            if thread is None or not thread.is_alive():
                self._thread = None
                self._stop_event.clear()
                state, message = "stopped", "Arka plan refactoring kuyruğu durduruldu."
            else:
                state, message = "stopping", "Çalışan refactoring işinin durması bekleniyor."
        self._status_call("set_state", self.SERVICE_NAME, state, message)

    def _finish_locked(
        self,
        job: RefactoringJob,
        state: RefactoringJobState,
        *,
        result: RefactoringJobResult | None = None,
        error: str = "",
    ) -> None:
        job.state = state
        job.result = result
        job.error = error
        job.finished_at = monotonic()
        self._active_keys.discard(job.key)
        job.done_event.set()

    def _is_idle(self) -> bool:
        with self._lock:
            return monotonic() - self._last_activity >= self._idle_seconds

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                while not self._stop_event.is_set() and not self._is_idle():
                    self._stop_event.wait(self._check_interval)
                if self._stop_event.is_set():
                    return

                try:
                    item = self._queue.get(timeout=self._check_interval)
                except Empty:
                    continue

                _, _, key = item
                if key is None:
                    self._queue.task_done()
                    return

                try:
                    if self._stop_event.is_set():
                        with self._lock:
                            pending = self._jobs.get(key)
                            if (
                                pending is not None
                                and pending.state is RefactoringJobState.QUEUED
                                and not pending.cancel_event.is_set()
                            ):
                                self._queue.put(
                                    (int(pending.priority), next(self._sequence), key)
                                )
                        continue

                    with self._lock:
                        job = self._jobs.get(key)
                        if job is None or job.state is not RefactoringJobState.QUEUED:
                            continue
                        job.state = RefactoringJobState.RUNNING
                        job.started_at = monotonic()

                    self._status_call(
                        "set_state",
                        self.SERVICE_NAME,
                        "running",
                        f"{key} çalışıyor.",
                        job_key=key,
                    )

                    try:
                        value = job.callback(job.cancel_event)
                        if job.cancel_event.is_set():
                            with self._lock:
                                self._finish_locked(job, RefactoringJobState.CANCELLED)
                            self._status_call(
                                "discarded",
                                self.SERVICE_NAME,
                                1,
                                f"{key} iptal edildi.",
                            )
                        else:
                            if isinstance(value, RefactoringJobResult):
                                result = value
                            else:
                                summary = self._safe_text(value, "Tamamlandı.").strip()
                                result = RefactoringJobResult(summary or "Tamamlandı.", value)
                            with self._lock:
                                self._finish_locked(
                                    job,
                                    RefactoringJobState.COMPLETED,
                                    result=result,
                                )
                            self._status_call(
                                "completed",
                                self.SERVICE_NAME,
                                1,
                                f"{key} tamamlandı; kullanıcı onayı bekleyen sonuç hazır.",
                            )
                    except Exception as exc:
                        error = self._safe_text(exc, exc.__class__.__name__).strip()
                        with self._lock:
                            self._finish_locked(
                                job,
                                RefactoringJobState.FAILED,
                                error=error or exc.__class__.__name__,
                            )
                        self._status_call("failed", self.SERVICE_NAME, exc, 1)
                finally:
                    self._queue.task_done()
        finally:
            with self._lock:
                if self._thread is current_thread():
                    self._thread = None
