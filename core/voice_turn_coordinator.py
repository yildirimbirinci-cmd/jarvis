from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

from artmach_assistant.core.conversation_runtime import ConversationRuntime
from artmach_assistant.core.task_orchestrator import (
    CancellationToken,
    TaskOrchestrator,
    TaskRecord,
)


class _VoiceController(Protocol):
    def begin_speech_session(self) -> str: ...

    def stop_speaking(self, session_id: str | None = None) -> bool: ...


@dataclass(frozen=True, slots=True)
class VoiceTurnBinding:
    turn_id: str = ""
    task_id: str = ""
    speech_session_id: str = ""


@dataclass(frozen=True, slots=True)
class PendingTurnRequest:
    command: str
    source: str = "voice"


class VoiceTurnCoordinator:
    """Bind one GUI request, background task and TTS session to one turn.

    Qt owns threads and signals, while this class owns the identity and
    cancellation rules.  Keeping that policy outside ``app.py`` makes stale
    callbacks testable without a microphone, a Qt event loop or an Ollama
    process.
    """

    def __init__(
        self,
        runtime: ConversationRuntime,
        orchestrator: TaskOrchestrator,
        voice: _VoiceController,
    ) -> None:
        self.runtime = runtime
        self.orchestrator = orchestrator
        self.voice = voice
        self._lock = threading.RLock()
        self._active_turn_id = ""
        self._active_task_id = ""
        self._active_speech_session_id = ""
        self._pending: PendingTurnRequest | None = None

    @property
    def binding(self) -> VoiceTurnBinding:
        with self._lock:
            return VoiceTurnBinding(
                self._active_turn_id,
                self._active_task_id,
                self._active_speech_session_id,
            )

    def begin_turn(self, command: str) -> str:
        clean = " ".join(str(command).split()).strip()
        if not clean:
            raise ValueError("Boş bir konuşma turu başlatılamaz.")
        turn_id = self.runtime.begin_turn(clean)
        with self._lock:
            self._active_turn_id = turn_id
            self._active_task_id = ""
            self._active_speech_session_id = ""
        return turn_id

    def is_current(self, turn_id: str | None) -> bool:
        return self.runtime.is_current(turn_id)

    def token_for(self, turn_id: str | None) -> object | None:
        return self.runtime.token_for(turn_id)

    def start_task(
        self,
        name: str,
        source: str,
        turn_id: str,
        *,
        timeout_seconds: float = 900.0,
    ) -> tuple[TaskRecord, CancellationToken]:
        if not self.runtime.is_current(turn_id):
            raise InterruptedError("Eski konuşma turu için görev başlatılamaz.")
        parent = self.runtime.token_for(turn_id)
        if parent is None:
            raise InterruptedError("Konuşma turunun iptal belirteci bulunamadı.")
        record, token = self.orchestrator.start(
            name,
            source,
            timeout_seconds,
            parent_token=parent,
            turn_id=turn_id,
        )
        if not self.runtime.begin_task(
            record.name,
            turn_id=turn_id,
            cancellation=token,
        ):
            self.orchestrator.cancel_active("konuşma turu geçersiz")
            raise InterruptedError("Görev yeni konuşma turuna bağlanamadı.")
        with self._lock:
            self._active_turn_id = turn_id
            self._active_task_id = record.task_id
        return record, token

    def finish_task(self, task_id: str, turn_id: str) -> bool:
        with self._lock:
            if task_id and task_id == self._active_task_id:
                self._active_task_id = ""
        if not self.runtime.is_current(turn_id):
            return False
        return self.runtime.finish_task_if_running(turn_id=turn_id)

    def begin_speech(self, turn_id: str) -> str:
        if not self.runtime.is_current(turn_id):
            return ""
        session_id = str(self.voice.begin_speech_session() or "").strip()
        if not session_id:
            return ""
        marked = self.runtime.mark_speaking(
            turn_id=turn_id,
            cancel_callback=lambda _reason, sid=session_id: self.voice.stop_speaking(sid),
        )
        if not marked:
            self.voice.stop_speaking(session_id)
            return ""
        with self._lock:
            self._active_turn_id = turn_id
            self._active_speech_session_id = session_id
        return session_id

    def speech_is_current(self, turn_id: str, session_id: str) -> bool:
        with self._lock:
            return bool(
                turn_id
                and session_id
                and turn_id == self._active_turn_id
                and session_id == self._active_speech_session_id
                and self.runtime.is_current(turn_id)
            )

    def complete_speech(
        self,
        turn_id: str,
        session_id: str,
        detail: str = "",
    ) -> bool:
        with self._lock:
            if (
                turn_id != self._active_turn_id
                or session_id != self._active_speech_session_id
            ):
                return False
            self._active_speech_session_id = ""
        return self.runtime.complete(detail, turn_id=turn_id)

    def fail_speech(self, turn_id: str, session_id: str, detail: str) -> bool:
        with self._lock:
            if (
                turn_id != self._active_turn_id
                or session_id != self._active_speech_session_id
            ):
                return False
            self._active_speech_session_id = ""
        return self.runtime.fail(detail, turn_id=turn_id)

    def queue_command(self, command: str, *, source: str = "voice") -> bool:
        clean = " ".join(str(command).split()).strip()
        if not clean:
            return False
        clean_source = str(source).strip().casefold()
        if clean_source not in {"voice", "keyboard"}:
            clean_source = "voice"
        with self._lock:
            # The newest owner request wins.  Keeping a long backlog would make
            # Jarvis execute obsolete commands after the user changed course.
            self._pending = PendingTurnRequest(clean, clean_source)
        return True

    def take_pending_request(self) -> PendingTurnRequest | None:
        with self._lock:
            request = self._pending
            self._pending = None
            return request

    def take_pending_command(self) -> str:
        request = self.take_pending_request()
        return request.command if request is not None else ""

    def has_pending_command(self) -> bool:
        with self._lock:
            return self._pending is not None

    def preempt(
        self,
        reason: str = "kullanıcı yeni bir cümle söyledi",
        *,
        pending_command: str = "",
        pending_source: str = "voice",
    ) -> bool:
        clean_reason = str(reason).strip() or "kullanıcı iptali"
        if pending_command:
            self.queue_command(pending_command, source=pending_source)
        with self._lock:
            turn_id = self._active_turn_id or self.runtime.current_turn_id
            session_id = self._active_speech_session_id
            self._active_speech_session_id = ""
        # Never use an unscoped stop here.  A stale callback must not stop a
        # newer speech session that happened to start at the same instant.
        speech_stopped = bool(
            session_id and self.voice.stop_speaking(session_id)
        )
        task_cancelled = self.orchestrator.cancel_active(clean_reason)
        turn_cancelled = self.runtime.cancel(
            clean_reason,
            turn_id=turn_id or None,
        )
        return bool(speech_stopped or task_cancelled or turn_cancelled)

    def clear_task_if_matches(self, task_id: str) -> bool:
        with self._lock:
            if not task_id or task_id != self._active_task_id:
                return False
            self._active_task_id = ""
            return True

    def clear_if_current(self, turn_id: str) -> None:
        with self._lock:
            if turn_id == self._active_turn_id:
                self._active_task_id = ""
                self._active_speech_session_id = ""
