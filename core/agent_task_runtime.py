from __future__ import annotations

import secrets
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from .tool_registry import PermissionLevel, ToolContext, ToolRegistry, ToolRegistryError


class AgentTaskError(RuntimeError):
    """Raised when an agent task cannot be prepared or executed."""


class TaskState(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class TaskProgress:
    phase: str = "bekliyor"
    current: int | None = None
    total: int | None = None
    message: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def percent(self) -> int | None:
        if self.current is None or self.total is None or self.total <= 0:
            return None
        return max(0, min(100, round((self.current / self.total) * 100)))


@dataclass(frozen=True, slots=True)
class TaskRequest:
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    requested_permission: PermissionLevel = PermissionLevel.READ
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreparedTask:
    task_id: str
    operation_id: str
    request: TaskRequest
    required_permission: PermissionLevel
    approval_token: str | None
    state: TaskState
    created_at: str


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    task_id: str
    operation_id: str
    tool_name: str
    state: TaskState
    progress: TaskProgress
    result: Any = None
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(slots=True)
class _TaskRecord:
    prepared: PreparedTask
    cancel_event: threading.Event
    snapshot: TaskSnapshot
    future: Future[Any] | None = None
    approval_token: str | None = None


class AgentTaskRuntime:
    """Runs registered tools with approval, cancellation and progress state.

    READ tools may be submitted directly. CHANGE and CRITICAL tools are first
    prepared and receive a one-time approval token. A tool never executes if
    the task was cancelled before dispatch.
    """

    def __init__(self, registry: ToolRegistry, *, max_workers: int = 4) -> None:
        if max_workers < 1:
            raise AgentTaskError("Worker sayısı en az 1 olmalı.")
        self.registry = registry
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="jarvis-tool")
        self._records: dict[str, _TaskRecord] = {}
        self._lock = threading.RLock()
        self._closed = False

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def prepare(self, request: TaskRequest) -> PreparedTask:
        with self._lock:
            if self._closed:
                raise AgentTaskError("Agent çalışma zamanı kapalı.")
        definition = self.registry.get(request.tool_name)
        requested = PermissionLevel(request.requested_permission)
        if requested < definition.permission:
            raise AgentTaskError(
                f"İstenen izin {definition.permission.name} gereksinimini karşılamıyor."
            )
        requires_approval = definition.permission >= PermissionLevel.CHANGE or definition.destructive
        task_id = secrets.token_hex(12)
        operation_id = secrets.token_hex(10)
        token = secrets.token_urlsafe(24) if requires_approval else None
        state = TaskState.PENDING_APPROVAL if requires_approval else TaskState.QUEUED
        prepared = PreparedTask(
            task_id=task_id,
            operation_id=operation_id,
            request=TaskRequest(
                tool_name=definition.name,
                arguments=dict(request.arguments),
                requested_permission=requested,
                metadata=dict(request.metadata),
            ),
            required_permission=definition.permission,
            approval_token=token,
            state=state,
            created_at=self._now(),
        )
        snapshot = TaskSnapshot(
            task_id=task_id,
            operation_id=operation_id,
            tool_name=definition.name,
            state=state,
            progress=TaskProgress(phase="onay bekleniyor" if requires_approval else "kuyrukta"),
            created_at=prepared.created_at,
        )
        with self._lock:
            self._records[task_id] = _TaskRecord(
                prepared=prepared,
                cancel_event=threading.Event(),
                snapshot=snapshot,
                approval_token=token,
            )
        if not requires_approval:
            self._dispatch(task_id)
        return prepared

    def approve(self, task_id: str, approval_token: str) -> TaskSnapshot:
        with self._lock:
            record = self._require(task_id)
            if record.snapshot.state is not TaskState.PENDING_APPROVAL:
                raise AgentTaskError("Görev onay beklemiyor.")
            if not record.approval_token or not secrets.compare_digest(
                record.approval_token, str(approval_token or "")
            ):
                raise AgentTaskError("Görev onay anahtarı geçersiz.")
            record.approval_token = None
            record.snapshot = replace(
                record.snapshot,
                state=TaskState.QUEUED,
                progress=TaskProgress(phase="kuyrukta"),
            )
        self._dispatch(task_id)
        return self.status(task_id)

    def _require(self, task_id: str) -> _TaskRecord:
        try:
            return self._records[task_id]
        except KeyError as exc:
            raise AgentTaskError("Görev bulunamadı.") from exc

    def _dispatch(self, task_id: str) -> None:
        with self._lock:
            record = self._require(task_id)
            if record.future is not None:
                raise AgentTaskError("Görev zaten kuyruğa alındı.")
            if record.cancel_event.is_set():
                record.snapshot = replace(
                    record.snapshot,
                    state=TaskState.CANCELLED,
                    progress=TaskProgress(phase="iptal edildi"),
                    finished_at=self._now(),
                )
                return
            record.future = self._executor.submit(self._execute, task_id)

    def _execute(self, task_id: str) -> Any:
        with self._lock:
            record = self._require(task_id)
            record.snapshot = replace(
                record.snapshot,
                state=TaskState.RUNNING,
                progress=TaskProgress(phase="çalışıyor"),
                started_at=self._now(),
            )
            prepared = record.prepared

        def report_progress(
            phase: str,
            current: int | None = None,
            total: int | None = None,
            message: str | None = None,
        ) -> None:
            if current is not None and current < 0:
                raise AgentTaskError("İlerleme değeri negatif olamaz.")
            if total is not None and total < 0:
                raise AgentTaskError("Toplam ilerleme değeri negatif olamaz.")
            if current is not None and total is not None and current > total:
                current = total
            with self._lock:
                current_record = self._require(task_id)
                current_record.snapshot = replace(
                    current_record.snapshot,
                    progress=TaskProgress(
                        phase=" ".join(str(phase or "çalışıyor").split()),
                        current=current,
                        total=total,
                        message=message,
                    ),
                )

        context = ToolContext(
            task_id=task_id,
            operation_id=prepared.operation_id,
            cancel_event=record.cancel_event,
            report_progress=report_progress,
            metadata=prepared.request.metadata,
        )
        definition = self.registry.get(prepared.request.tool_name)
        try:
            result = self.registry.invoke(
                definition.name,
                context,
                prepared.request.arguments,
                granted_permission=prepared.request.requested_permission,
            )
            context.raise_if_cancelled()
        except ToolRegistryError as exc:
            cancelled = record.cancel_event.is_set()
            with self._lock:
                record.snapshot = replace(
                    record.snapshot,
                    state=TaskState.CANCELLED if cancelled else TaskState.FAILED,
                    progress=TaskProgress(phase="iptal edildi" if cancelled else "başarısız"),
                    error=str(exc),
                    finished_at=self._now(),
                )
            if cancelled:
                return None
            raise
        except Exception as exc:
            with self._lock:
                record.snapshot = replace(
                    record.snapshot,
                    state=TaskState.FAILED,
                    progress=TaskProgress(phase="başarısız"),
                    error=f"{type(exc).__name__}: {exc}",
                    finished_at=self._now(),
                )
            raise
        else:
            with self._lock:
                record.snapshot = replace(
                    record.snapshot,
                    state=TaskState.SUCCEEDED,
                    progress=TaskProgress(phase="tamamlandı", current=1, total=1),
                    result=result,
                    finished_at=self._now(),
                )
            return result

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            record = self._require(task_id)
            if record.snapshot.state in {
                TaskState.SUCCEEDED,
                TaskState.FAILED,
                TaskState.CANCELLED,
                TaskState.TIMED_OUT,
            }:
                return False
            record.cancel_event.set()
            if record.snapshot.state in {TaskState.PENDING_APPROVAL, TaskState.QUEUED}:
                if record.future is None or record.future.cancel():
                    record.snapshot = replace(
                        record.snapshot,
                        state=TaskState.CANCELLED,
                        progress=TaskProgress(phase="iptal edildi"),
                        finished_at=self._now(),
                    )
            return True

    def status(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            return replace(self._require(task_id).snapshot)

    def wait(self, task_id: str, *, timeout: float | None = None) -> TaskSnapshot:
        with self._lock:
            record = self._require(task_id)
            future = record.future
            if record.snapshot.state is TaskState.PENDING_APPROVAL:
                raise AgentTaskError("Görev henüz onaylanmadı.")
        if future is not None:
            try:
                future.result(timeout=timeout)
            except FutureTimeoutError:
                with self._lock:
                    current = self._require(task_id)
                    current.cancel_event.set()
                    current.snapshot = replace(
                        current.snapshot,
                        state=TaskState.TIMED_OUT,
                        progress=TaskProgress(phase="zaman aşımı"),
                        error="Görev bekleme süresini aştı.",
                        finished_at=self._now(),
                    )
            except Exception:
                pass
        return self.status(task_id)

    def list_tasks(self, *, include_finished: bool = True) -> tuple[TaskSnapshot, ...]:
        with self._lock:
            values = tuple(replace(record.snapshot) for record in self._records.values())
        if not include_finished:
            values = tuple(
                item for item in values
                if item.state not in {
                    TaskState.SUCCEEDED,
                    TaskState.FAILED,
                    TaskState.CANCELLED,
                    TaskState.TIMED_OUT,
                }
            )
        return tuple(sorted(values, key=lambda item: item.created_at))

    def prune_finished(self) -> int:
        with self._lock:
            finished = {
                task_id for task_id, record in self._records.items()
                if record.snapshot.state in {
                    TaskState.SUCCEEDED,
                    TaskState.FAILED,
                    TaskState.CANCELLED,
                    TaskState.TIMED_OUT,
                }
            }
            for task_id in finished:
                self._records.pop(task_id, None)
            return len(finished)

    def close(self, *, cancel_running: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if cancel_running:
                for record in self._records.values():
                    record.cancel_event.set()
        self._executor.shutdown(wait=True, cancel_futures=cancel_running)

    def __enter__(self) -> "AgentTaskRuntime":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close(cancel_running=exc is not None)
