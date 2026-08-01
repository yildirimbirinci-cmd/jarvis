from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .agent_task_runtime import (
    AgentTaskError,
    AgentTaskRuntime,
    PreparedTask,
    TaskRequest,
    TaskSnapshot,
    TaskState,
)
from .tool_registry import PermissionLevel


class AgentToolSessionError(RuntimeError):
    """Raised when a conversational tool session cannot continue safely."""


@dataclass(frozen=True, slots=True)
class SessionTaskView:
    task_id: str
    operation_id: str
    tool_name: str
    state: TaskState
    approval_required: bool
    summary: str
    progress_percent: int | None = None
    result: Any = None
    error: str | None = None


@dataclass(slots=True)
class _PendingApproval:
    task_id: str
    approval_token: str
    expires_at: datetime


class AgentToolSession:
    """Conversation-facing facade for :class:`AgentTaskRuntime`.

    Approval tokens stay inside this object and are never returned to the model
    or UI. A user may approve, cancel or inspect the most recently prepared
    task using stable conversational commands.
    """

    _FINISHED = {
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.TIMED_OUT,
    }

    def __init__(
        self,
        runtime: AgentTaskRuntime,
        *,
        approval_ttl_seconds: int = 300,
    ) -> None:
        if approval_ttl_seconds < 10:
            raise AgentToolSessionError("Onay süresi en az 10 saniye olmalı.")
        self.runtime = runtime
        self.approval_ttl = timedelta(seconds=approval_ttl_seconds)
        self._pending: dict[str, _PendingApproval] = {}
        self._latest_task_id: str | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def submit(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        requested_permission: PermissionLevel = PermissionLevel.READ,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionTaskView:
        prepared = self.runtime.prepare(
            TaskRequest(
                tool_name=tool_name,
                arguments=dict(arguments or {}),
                requested_permission=requested_permission,
                metadata=dict(metadata or {}),
            )
        )
        with self._lock:
            self._latest_task_id = prepared.task_id
            if prepared.approval_token:
                self._pending[prepared.task_id] = _PendingApproval(
                    task_id=prepared.task_id,
                    approval_token=prepared.approval_token,
                    expires_at=self._now() + self.approval_ttl,
                )
        return self._view(prepared, self.runtime.status(prepared.task_id))

    def approve_latest(self) -> SessionTaskView:
        task_id = self._require_latest()
        with self._lock:
            pending = self._pending.get(task_id)
            if pending is None:
                snapshot = self.runtime.status(task_id)
                if snapshot.state is TaskState.PENDING_APPROVAL:
                    raise AgentToolSessionError("Görev onay anahtarı artık kullanılamıyor.")
                return self._view(None, snapshot)
            if self._now() >= pending.expires_at:
                self._pending.pop(task_id, None)
                self.runtime.cancel(task_id)
                raise AgentToolSessionError("Görev onayı zaman aşımına uğradı ve işlem iptal edildi.")
            token = pending.approval_token
            self._pending.pop(task_id, None)
        try:
            snapshot = self.runtime.approve(task_id, token)
        except AgentTaskError as exc:
            raise AgentToolSessionError(str(exc)) from exc
        return self._view(None, snapshot)

    def cancel_latest(self) -> SessionTaskView:
        task_id = self._require_latest()
        with self._lock:
            self._pending.pop(task_id, None)
        try:
            self.runtime.cancel(task_id)
            snapshot = self.runtime.status(task_id)
        except AgentTaskError as exc:
            raise AgentToolSessionError(str(exc)) from exc
        return self._view(None, snapshot)

    def status_latest(self) -> SessionTaskView:
        task_id = self._require_latest()
        try:
            snapshot = self.runtime.status(task_id)
        except AgentTaskError as exc:
            raise AgentToolSessionError(str(exc)) from exc
        if snapshot.state in self._FINISHED:
            with self._lock:
                self._pending.pop(task_id, None)
        return self._view(None, snapshot)

    def wait_latest(self, *, timeout: float | None = None) -> SessionTaskView:
        task_id = self._require_latest()
        try:
            snapshot = self.runtime.wait(task_id, timeout=timeout)
        except AgentTaskError as exc:
            raise AgentToolSessionError(str(exc)) from exc
        if snapshot.state in self._FINISHED:
            with self._lock:
                self._pending.pop(task_id, None)
        return self._view(None, snapshot)

    def clear_latest(self) -> bool:
        with self._lock:
            if self._latest_task_id is None:
                return False
            self._pending.pop(self._latest_task_id, None)
            self._latest_task_id = None
            return True

    def _require_latest(self) -> str:
        with self._lock:
            if self._latest_task_id is None:
                raise AgentToolSessionError("Takip edilen bir araç görevi yok.")
            return self._latest_task_id

    @classmethod
    def _view(
        cls,
        prepared: PreparedTask | None,
        snapshot: TaskSnapshot,
    ) -> SessionTaskView:
        approval_required = snapshot.state is TaskState.PENDING_APPROVAL
        percent = snapshot.progress.percent
        summary = cls._summary(snapshot)
        return SessionTaskView(
            task_id=snapshot.task_id,
            operation_id=snapshot.operation_id,
            tool_name=snapshot.tool_name,
            state=snapshot.state,
            approval_required=approval_required,
            summary=summary,
            progress_percent=percent,
            result=snapshot.result,
            error=snapshot.error,
        )

    @staticmethod
    def _summary(snapshot: TaskSnapshot) -> str:
        if snapshot.state is TaskState.PENDING_APPROVAL:
            return f"{snapshot.tool_name} görevi onay bekliyor."
        if snapshot.state is TaskState.QUEUED:
            return f"{snapshot.tool_name} görevi kuyruğa alındı."
        if snapshot.state is TaskState.RUNNING:
            progress = snapshot.progress
            if progress.percent is not None:
                return f"{snapshot.tool_name}: {progress.phase} %{progress.percent}."
            if progress.message:
                return f"{snapshot.tool_name}: {progress.phase} — {progress.message}."
            return f"{snapshot.tool_name}: {progress.phase}."
        if snapshot.state is TaskState.SUCCEEDED:
            return f"{snapshot.tool_name} görevi tamamlandı."
        if snapshot.state is TaskState.CANCELLED:
            return f"{snapshot.tool_name} görevi iptal edildi."
        if snapshot.state is TaskState.TIMED_OUT:
            return f"{snapshot.tool_name} görevi zaman aşımına uğradı."
        if snapshot.state is TaskState.FAILED:
            return f"{snapshot.tool_name} görevi başarısız: {snapshot.error or 'bilinmeyen hata'}."
        return f"{snapshot.tool_name} görevi {snapshot.state.value} durumunda."
